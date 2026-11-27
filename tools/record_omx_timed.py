#!/usr/bin/env python3
"""표준 LeRobot 기록에 실제 수집 시각을 연결하는 검증 로그를 추가한다."""

import argparse
from collections import OrderedDict
from functools import wraps
import json
import os
from pathlib import Path
import stat
import sys
import threading
import time
import uuid
import weakref


class RecordTiming:
    """센서값과 제어 명령을 바꾸지 않고 동일 프로세스의 단조 시각을 기록한다."""

    def __init__(self, log_dir, *, readable_videos=False):
        path = Path(log_dir)
        path.mkdir(parents=True, exist_ok=True)
        self.stream = (path / "record_timing.jsonl").open("x", encoding="utf-8")
        self.local = threading.local()
        self.frames = OrderedDict()
        self.lock = threading.Lock()
        self.patches = []
        self.attempt = None
        self.readable_videos = readable_videos
        self.dataset_roots = set()
        self.emit("session", monotonic_ns=time.perf_counter_ns(), unix_ns=time.time_ns(),
                  camera_time_definition="호스트 수신 처리 구간이며 센서 노출 시각은 아닙니다.")

    def emit(self, event, **values):
        self.stream.write(json.dumps({"event": event, **values}, ensure_ascii=False) + "\n")
        self.stream.flush()

    def replace(self, owner, name, method):
        self.patches.append((owner, name, name in owner.__dict__, getattr(owner, name)))
        setattr(owner, name, method)

    def allow_video_reading(self):
        """이번 실행에서 기록한 데이터셋의 일반 MP4 파일만 읽을 수 있게 한다."""
        if not self.readable_videos:
            return
        for root in self.dataset_roots:
            videos = root / "videos"
            if videos.is_symlink():
                continue
            for path in videos.rglob("*.mp4"):
                try:
                    if path.is_symlink() or not path.is_file():
                        continue
                    path.resolve().relative_to(root)
                    # 인코더가 0600으로 생성한 파일도 호스트에서 읽도록 한다.
                    mode = stat.S_IMODE(path.stat().st_mode)
                    if mode & 0o444 != 0o444:
                        path.chmod(mode | 0o444)
                except (OSError, ValueError) as error:
                    print(f"영상 읽기 권한 적용 실패: {path}: {error}", file=sys.stderr)

    def install(self, robot_class, bus_class, camera_class, dataset_class):
        def timed(owner, name, field):
            original = getattr(owner, name)

            @wraps(original)
            def measured(*args, **kwargs):
                row = getattr(self.local, "row", None)
                start = time.perf_counter_ns()
                try:
                    return original(*args, **kwargs)
                finally:
                    if row is not None and (
                        field != "bus_read_intervals_ns" or args[0] is self.local.follower_bus
                    ):
                        row.setdefault(field, []).append([start, time.perf_counter_ns()])
            self.replace(owner, name, measured)

        original_observe = robot_class.get_observation

        @wraps(original_observe)
        def observe(robot, *args, **kwargs):
            row = {"observation_start_ns": time.perf_counter_ns(), "cameras": {}}
            self.local.row = row
            self.local.follower_bus = robot.bus
            self.local.camera_names = {id(cam): name for name, cam in robot.cameras.items()}
            try:
                return original_observe(robot, *args, **kwargs)
            except BaseException as error:
                self.emit("observation_error", error=type(error).__name__, **row)
                raise
            finally:
                row["observation_end_ns"] = time.perf_counter_ns()
        self.replace(robot_class, "get_observation", observe)
        timed(bus_class, "sync_read", "bus_read_intervals_ns")
        timed(robot_class, "send_action", "action_send_intervals_ns")

        original_read = camera_class._read_from_hardware

        @wraps(original_read)
        def hardware(camera, *args, **kwargs):
            start = time.perf_counter_ns()
            result = original_read(camera, *args, **kwargs)
            self.local.capture = (id(camera), start, time.perf_counter_ns())
            return result
        self.replace(camera_class, "_read_from_hardware", hardware)
        original_process = camera_class._postprocess_image

        @wraps(original_process)
        def process(camera, *args, **kwargs):
            result = original_process(camera, *args, **kwargs)
            capture = getattr(self.local, "capture", None)
            if capture is not None and capture[0] == id(camera):
                with self.lock:
                    key = (id(camera), id(result))
                    self.frames[key] = (
                        weakref.ref(result), capture[1], capture[2], time.perf_counter_ns())
                    # 메모리 주소가 재사용돼 기존 키를 갱신해도 최신 항목으로 취급한다.
                    self.frames.move_to_end(key)
                    while len(self.frames) > 256:
                        self.frames.popitem(last=False)
            return result
        self.replace(camera_class, "_postprocess_image", process)
        def trace_camera_read(method_name):
            original = getattr(camera_class, method_name)

            @wraps(original)
            def latest(camera, *args, **kwargs):
                result = original(camera, *args, **kwargs)
                returned = time.perf_counter_ns()
                row = getattr(self.local, "row", None)
                if row is not None:
                    name = self.local.camera_names.get(id(camera), "unknown")
                    with self.lock:
                        entry = self.frames.get((id(camera), id(result)))
                    valid = entry is not None and entry[0]() is result
                    row["cameras"][name] = {
                        "matched_frame": valid,
                        "hardware_read_start_ns": entry[1] if valid else None,
                        "hardware_read_end_ns": entry[2] if valid else None,
                        "postprocess_end_ns": entry[3] if valid else None,
                        "observation_return_ns": returned,
                    }
                return result
            self.replace(camera_class, method_name, latest)

        # OMX의 실제 관측 경로와 대기식 읽기 경로를 모두 추적한다.
        trace_camera_read("read_latest")
        trace_camera_read("async_read")
        original_add = dataset_class.add_frame

        @wraps(original_add)
        def add(dataset, frame, *args, **kwargs):
            self.dataset_roots.add(Path(dataset.root).resolve())
            row = dict(getattr(self.local, "row", {}) or {})
            expected = set(getattr(self.local, "camera_names", {}).values())
            cameras = row.get("cameras", {})
            if (not row or not expected or set(cameras) != expected
                    or not all(camera.get("matched_frame") for camera in cameras.values())):
                # 누락된 시간 로그로 수집을 계속하지 않고 즉시 원인을 알린다.
                self.emit("frame_timing_error", dataset_root=str(dataset.root),
                          episode_index=dataset.num_episodes,
                          expected_cameras=sorted(expected), **row)
                raise RuntimeError("영상 프레임의 수신 시각 연결이 누락됐습니다. 기록을 중단합니다.")
            buffer = dataset.writer.episode_buffer
            index = buffer["size"] if buffer else 0
            if index == 0:
                self.attempt = uuid.uuid4().hex
            start = time.perf_counter_ns()
            result = original_add(dataset, frame, *args, **kwargs)
            self.emit("buffered_frame", attempt=self.attempt, episode_index=dataset.num_episodes,
                      frame_index=index, dataset_root=str(dataset.root),
                      add_start_ns=start, add_end_ns=time.perf_counter_ns(), **row)
            self.local.row = None
            return result
        self.replace(dataset_class, "add_frame", add)
        original_save = dataset_class.save_episode

        @wraps(original_save)
        def save(dataset, *args, **kwargs):
            episode = dataset.num_episodes
            result = original_save(dataset, *args, **kwargs)
            self.allow_video_reading()
            self.emit("saved_episode", attempt=self.attempt, episode_index=episode,
                      dataset_root=str(dataset.root), monotonic_ns=time.perf_counter_ns())
            return result
        self.replace(dataset_class, "save_episode", save)

    def close(self):
        for owner, name, owned, original in reversed(self.patches):
            if owned:
                setattr(owner, name, original)
            else:
                delattr(owner, name)
        self.patches.clear()
        self.stream.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timing-dir", required=True, help="실측 시각 검증 로그 저장 폴더")
    parser.add_argument("--check-only", action="store_true", help="장치 연결 없이 훅 구성과 복원만 확인")
    parser.add_argument("--readable-videos", action="store_true", help="이번 데이터셋의 MP4에 읽기 권한 자동 적용")
    args, rest = parser.parse_known_args()
    if rest[:1] == ["--"]:
        rest = rest[1:]
    from lerobot.cameras.opencv.camera_opencv import OpenCVCamera
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    from lerobot.motors.dynamixel.dynamixel import DynamixelMotorsBus
    from lerobot.robots.omx_follower.omx_follower import OmxFollower
    from lerobot.scripts.lerobot_record import main as record_main

    if args.readable_videos:
        # 일반 신규 파일의 기본 권한. 인코더의 명시적 0600은 저장 후 별도로 보정한다.
        os.umask(0o022)
    profiler = RecordTiming(args.timing_dir, readable_videos=args.readable_videos)
    try:
        profiler.install(OmxFollower, DynamixelMotorsBus, OpenCVCamera, LeRobotDataset)
        if args.check_only:
            print("접속 없이 시간 기록 훅 구성을 확인했습니다.")
            return
        sys.argv = [sys.argv[0], *rest]
        print("실제 로봇 시연을 기록합니다. 시간 로그는 별도 폴더에 저장합니다.", flush=True)
        record_main()
    finally:
        # 최종 영상 병합이 record 종료 과정에서 수행되는 경우도 포함한다.
        profiler.allow_video_reading()
        profiler.close()


if __name__ == "__main__":
    main()
