#!/usr/bin/env python3
"""기존 LeRobot 실행의 관측·목표값·영상을 추가 장치 조회 없이 기록한다."""

import argparse
from collections import OrderedDict
import csv
from dataclasses import asdict, is_dataclass
from fractions import Fraction
from functools import wraps
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import queue
import select
import sys
import threading
import time
import weakref


JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")


def positions(values):
    """원래 관절값의 단위를 유지하며 CSV 열 순서를 고정한다."""
    return [float(values[f"{name}.pos"]) if f"{name}.pos" in values else "" for name in JOINTS]


def plain(value):
    if is_dataclass(value):
        return plain(asdict(value))
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "value"):
        return plain(value.value)
    return str(value)


class TerminalEvaluation:
    """TTY 입력만 읽고 로봇 접근 없이 평가 콜백을 호출한다. Ctrl+C 신호는 유지한다."""

    def __init__(self, callback, stream=None):
        self.callback = callback
        self.stream = sys.stdin if stream is None else stream
        self.stop_event = threading.Event()
        self.thread = None
        self.fd = None
        self.original_mode = None
        self.escape = 0
        self.error = None

    def start(self):
        try:
            import termios
            import tty
        except ImportError as error:
            self.error = str(error)
            return False
        try:
            if not self.stream.isatty():
                return False
            self.fd = self.stream.fileno()
            if os.tcgetpgrp(self.fd) != os.getpgrp():
                raise OSError("백그라운드 프로세스에서는 평가 키를 읽지 않습니다.")
            self.original_mode = termios.tcgetattr(self.fd)
            termios.tcflush(self.fd, termios.TCIFLUSH)
            tty.setcbreak(self.fd, termios.TCSANOW)
            self.thread = threading.Thread(target=self.listen, name="omx-manual-evaluation", daemon=True)
            self.thread.start()
            return True
        except (OSError, ValueError, RuntimeError, termios.error) as error:
            self.error = str(error)
            self.stop()
            return False

    def listen(self):
        try:
            while not self.stop_event.is_set():
                ready, _, _ = select.select([self.fd], [], [], 0.1)
                if not ready:
                    continue
                data = os.read(self.fd, 1)
                if not data:
                    break
                key = data[0]
                # 방향키·기능키·Alt 조합의 일부 문자를 평가 입력으로 오인하지 않는다.
                if key == 27:
                    self.escape = 1
                    continue
                if self.escape == 1:
                    self.escape = 2 if key in (ord("["), ord("O")) else 0
                    continue
                if self.escape == 2:
                    if 0x40 <= key <= 0x7E:
                        self.escape = 0
                    continue
                key = chr(key).lower()
                if key in {"s", "f", "q"}:
                    self.callback(key)
        except Exception as error:
            self.error = str(error)
            print(f"수동 평가 키 입력이 중단됐습니다. Ctrl+C로 종료할 수 있습니다: {error}", file=sys.stderr)

    def stop(self):
        self.stop_event.set()
        try:
            if self.thread is not None and self.thread.ident is not None:
                self.thread.join(timeout=1)
        except RuntimeError as error:
            self.error = str(error)
        finally:
            if self.original_mode is not None:
                import termios
                try:
                    termios.tcsetattr(self.fd, termios.TCSANOW, self.original_mode)
                    termios.tcflush(self.fd, termios.TCIFLUSH)
                except (OSError, ValueError, termios.error) as error:
                    self.error = str(error)
                self.original_mode = None


class RolloutRecord:
    """CSV는 주 제어 경로에서 기록하고 영상 인코딩은 별도 스레드로 분리한다."""

    def __init__(self, log_dir, *, video_queue_size=32, av_module=None, manual_evaluation=False):
        self.path = Path(log_dir)
        self.path.mkdir(parents=True, exist_ok=True)
        if any((self.path / name).exists() for name in
               ("run_metadata.json", "evaluation.json", "observations.csv", "actions.csv", "camera_timing.csv", "videos")):
            raise FileExistsError("기존 검증 기록을 덮어쓰지 않습니다. 새 로그 폴더를 사용하세요.")
        if av_module is None:
            import av as av_module
        self.av = av_module
        self.origin_ns = time.perf_counter_ns()
        self.phase = "startup"
        self.manual_evaluation = manual_evaluation
        self.evaluation_lock = threading.Lock()
        self.manual_stop = threading.Event()
        self.shutdown_event = None
        self.control_start_ns = None
        self.keys = None
        self.evaluation = {"outcome": "not_assessed", "source": "unlabeled",
                           "termination_reason": "pending", "decision_monotonic_ns": None,
                           "control_elapsed_sec": None, "failure_stage": "unspecified"}
        self.local = threading.local()
        self.frame_lock = threading.Lock()
        self.frames = OrderedDict()
        self.patches = []
        self.streams = []
        self.observation_index = 0
        self.action_index = 0
        self.latest_observation = None
        self.camera_ids = {}
        self.video_jobs = queue.Queue(maxsize=video_queue_size)
        self.video_error = None
        self.record_error = None
        self.video_stop = threading.Event()
        self.video_drops = 0
        self.unmatched_frames = 0
        self.video_counts = {}
        self.closed = False
        self.worker = None
        self.last_flush = time.monotonic()
        self.metadata = {
            "schema_version": 1, "status": "recording", "origin_monotonic_ns": self.origin_ns,
            "origin_unix_ns": time.time_ns(), "joints": list(JOINTS),
            "success": "not_assessed",
            "manual_evaluation_requested": manual_evaluation,
            "time_definition": "단조 시계의 호스트 호출·수신 시각. 카메라 노출 시각이 아닙니다.",
            "action_definition": "실제로 실행된 제어 목표. 전체 예측 묶음이 아닌 로봇 API 입력입니다.",
            "video_definition": "관측에 사용한 영상만 저장. 카메라의 모든 30 FPS 프레임이 아닙니다.",
            "response_definition": "전송 API 성공은 모터의 목표 도달 또는 수신 확인을 뜻하지 않습니다.",
        }
        try:
            self.obs_csv = self.csv("observations.csv", [
                "observation_index", "phase", "time_sec", "start_ns", "end_ns", "state_start_ns", "state_end_ns",
                *[f"actual_{j}" for j in JOINTS]])
            self.action_csv = self.csv("actions.csv", [
                "action_index", "phase", "observation_index", "observation_end_ns", "time_sec",
                "start_ns", "end_ns", "goal_write_start_ns", "goal_write_end_ns", "status", "error_type",
                *[f"actual_{j}" for j in JOINTS], *[f"requested_{j}" for j in JOINTS],
                *[f"sent_{j}" for j in JOINTS]])
            self.camera_csv = self.csv("camera_timing.csv", [
                "observation_index", "phase", "camera", "observation_end_ns", "hardware_read_start_ns",
                "hardware_read_end_ns", "postprocess_end_ns", "matched_frame", "reused_frame", "video_queued"])
            self.previous_frames = {}
            self.video_csv = self.csv("video_frames.csv", [
                "camera", "file", "video_frame_index", "observation_index", "phase", "observation_end_ns", "pts_us"])
            self.save_metadata()
            self.save_evaluation()
            self.worker = threading.Thread(target=self.write_videos, name="omx-video-record", daemon=True)
            self.worker.start()
        except BaseException:
            self.closed = True
            for stream in self.streams:
                try:
                    stream.close()
                except OSError:
                    pass
            raise

    def csv(self, name, columns):
        stream = (self.path / name).open("x", encoding="utf-8", newline="")
        self.streams.append(stream)
        writer = csv.writer(stream)
        writer.writerow(columns)
        return writer

    def save_metadata(self):
        temp = self.path / "run_metadata.json.tmp"
        temp.write_text(json.dumps(plain(self.metadata), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path / "run_metadata.json")

    def save_evaluation(self):
        temp = self.path / "evaluation.json.tmp"
        temp.write_text(json.dumps(plain(self.evaluation), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path / "evaluation.json")

    def evaluate(self, key):
        """최초 수동 판정만 채택하고 native 종료 이벤트를 설정한다."""
        if not self.manual_evaluation or key not in {"s", "f", "q"}:
            return False
        with self.evaluation_lock:
            if (self.phase != "control" or self.manual_stop.is_set() or self.shutdown_event is None
                    or self.shutdown_event.is_set()):
                return False
            if self.action_index == 0:
                print("첫 명령 전에는 평가하지 않습니다. 종료는 Ctrl+C를 사용하세요.", flush=True)
                return False
            now = time.perf_counter_ns()
            outcome = {"s": "success", "f": "failure", "q": "not_assessed"}[key]
            self.evaluation.update({
                "outcome": outcome, "source": "manual_keyboard",
                "termination_reason": "manual_quit" if key == "q" else "manual_evaluation",
                "decision_monotonic_ns": now,
                "control_elapsed_sec": (now - self.control_start_ns) / 1e9,
                "latest_observation_index": self.latest_observation[0] if self.latest_observation else None,
                "last_started_action_index": self.action_index - 1,
            })
            self.metadata["success"] = outcome
            self.manual_stop.set()
            self.shutdown_event.set()
            label = {"s": "성공", "f": "실패", "q": "판정 없는 중단"}[key]
            print(f"수동 평가: {label}. 추론 종료와 설정된 초기 자세 복귀를 진행합니다.", flush=True)
            return True

    def flush(self):
        # 영상 전용 CSV는 종료 때 작업 스레드가 끝난 후 함께 비운다.
        for stream in self.streams[:-1]:
            stream.flush()
        self.last_flush = time.monotonic()

    def check_writer(self):
        if self.phase != "control":
            return
        if self.video_error is not None:
            raise RuntimeError("영상 기록에 실패했습니다. run_metadata.json을 확인하세요.") from self.video_error
        if time.monotonic() - self.last_flush >= 1:
            self.flush()

    def write_videos(self):
        outputs = {}
        try:
            while True:
                try:
                    item = self.video_jobs.get(timeout=0.1)
                except queue.Empty:
                    if self.video_stop.is_set():
                        break
                    continue
                if item is None:
                    break
                name, image, obs_index, phase, stamp_ns, fps, color = item
                if name not in outputs:
                    folder = self.path / "videos"
                    folder.mkdir(exist_ok=True)
                    file = f"videos/{name}.mp4"
                    container = self.av.open(str(self.path / file), mode="w")
                    outputs[name] = [container, None, -1, file, stamp_ns]
                    stream = container.add_stream("libx264", rate=max(1, round(fps)))
                    stream.width, stream.height = image.shape[1], image.shape[0]
                    stream.pix_fmt = "yuv420p"
                    stream.time_base = Fraction(1, 1_000_000)
                    stream.codec_context.time_base = stream.time_base
                    stream.options = {"preset": "ultrafast", "crf": "23", "bf": "0"}
                    outputs[name][1] = stream
                    self.video_counts[name] = 0
                container, stream, last_pts, file, video_origin_ns = outputs[name]
                frame = self.av.VideoFrame.from_ndarray(image, format="bgr24" if color == "bgr" else "rgb24")
                pts = max(last_pts + 1, (stamp_ns - video_origin_ns) // 1000)
                frame.pts, frame.time_base = pts, Fraction(1, 1_000_000)
                for packet in stream.encode(frame):
                    container.mux(packet)
                index = self.video_counts[name]
                self.video_csv.writerow([name, file, index, obs_index, phase, stamp_ns, pts])
                self.video_counts[name] = index + 1
                outputs[name][2] = pts
        except BaseException as error:
            self.video_error = error
        finally:
            for container, stream, _, _, _ in outputs.values():
                try:
                    if stream is not None:
                        for packet in stream.encode():
                            container.mux(packet)
                    container.close()
                except BaseException as error:
                    if self.video_error is None:
                        self.video_error = error
            for file in (self.path / "videos").glob("*.mp4"):
                try:
                    file.chmod(file.stat().st_mode | 0o444)
                except OSError as error:
                    if self.video_error is None:
                        self.video_error = error

    def replace(self, owner, name, replacement):
        self.patches.append((owner, name, name in owner.__dict__, getattr(owner, name)))
        setattr(owner, name, replacement)

    def capture_metadata(self, ctx):
        robot = ctx.hardware.robot_wrapper.inner
        cfg = ctx.runtime.cfg
        policy_path = Path(str(cfg.policy.pretrained_path))
        versions = {}
        for name in ["lerobot", "torch", "peft", "transformers", "av", "dynamixel-sdk"]:
            try:
                versions[name] = version(name)
            except PackageNotFoundError:
                versions[name] = None
        hashes = {}
        for name in ["config.json", "adapter_config.json", "adapter_model.safetensors",
                     "policy_preprocessor.json", "policy_postprocessor.json"]:
            file = policy_path / name
            if file.is_file():
                hashes[name] = hashlib.sha256(file.read_bytes()).hexdigest()
        camera_config = {}
        self.camera_ids = {id(cam): name for name, cam in robot.cameras.items()}
        for name, camera in robot.cameras.items():
            if not name.replace("_", "").isalnum():
                raise ValueError("영상 파일 이름에 사용할 수 없는 카메라 이름입니다.")
            camera_config[name] = {key: plain(getattr(camera.config, key, None)) for key in
                                   ("index_or_path", "width", "height", "fps", "fourcc", "color_mode", "rotation")}
        self.metadata.update({
            "task": cfg.task, "policy_path": str(policy_path), "model_sha256": hashes,
            "control_fps_requested": cfg.fps, "inference": plain(cfg.inference),
            "policy_device": plain(cfg.policy.device), "policy_use_amp": cfg.policy.use_amp,
            "execution_device": plain(cfg.device),
            "rename_map": plain(cfg.rename_map), "camera_config": camera_config,
            "robot_id": robot.config.id, "robot_port": robot.config.port,
            "use_degrees": robot.config.use_degrees,
            "max_relative_target": plain(robot.config.max_relative_target),
            "calibration": plain(robot.calibration),
            "bus_calibration": plain(robot.bus.calibration),
            "motor_configuration": plain(robot.bus.motors), "versions": versions,
            "initial_position": plain(ctx.hardware.initial_position),
            "return_to_initial_position": cfg.return_to_initial_position,
            "position_unit": "body: degrees" if robot.config.use_degrees else "body: normalized -100..100",
            "gripper_unit": "normalized 0..100",
        })
        self.save_metadata()

    def install(self, robot_class, bus_class, camera_class, strategy_class):
        original_run = strategy_class.run

        @wraps(original_run)
        def run(strategy, ctx):
            try:
                self.capture_metadata(ctx)
                self.shutdown_event = ctx.runtime.shutdown_event
                self.control_start_ns = time.perf_counter_ns()
                self.phase = "control"
                if self.manual_evaluation:
                    self.keys = TerminalEvaluation(self.evaluate)
                    active = self.keys.start()
                    self.metadata["manual_keys_active"] = active
                    if active:
                        print("수동 평가 키: s=성공, f=실패, q=판정 없이 종료. 실행 터미널에 포커스를 두세요.", flush=True)
                    else:
                        print("TTY 입력을 사용할 수 없습니다. 판정 없이 Ctrl+C로 종료하세요.", flush=True)
                return original_run(strategy, ctx)
            finally:
                self.phase = "shutdown"
                with self.evaluation_lock:
                    if self.evaluation["termination_reason"] == "pending":
                        self.evaluation["termination_reason"] = (
                            "shutdown_requested_without_label" if self.shutdown_event is not None
                            and self.shutdown_event.is_set() else "run_ended_without_label")
                    try:
                        # 입력 스레드는 종료 요청만 하고 파일은 주 스레드가 복귀 전에 저장한다.
                        self.save_evaluation()
                    except OSError as error:
                        self.record_error = str(error)
                        print(f"수동 판정 저장 실패: {error}", file=sys.stderr)
        self.replace(strategy_class, "run", run)

        original_observe = robot_class.get_observation

        @wraps(original_observe)
        def observe(robot, *args, **kwargs):
            self.check_writer()
            start = time.perf_counter_ns()
            self.local.observation = {"bus": robot.bus, "start": None, "end": None}
            try:
                result = original_observe(robot, *args, **kwargs)
                end = time.perf_counter_ns()
                if self.phase not in {"control", "shutdown"}:
                    return result
                try:
                    idx = self.observation_index
                    self.observation_index += 1
                    state = positions(result)
                    self.latest_observation = (idx, end, state)
                    interval = self.local.observation
                    self.obs_csv.writerow([idx, self.phase, (end - self.origin_ns) / 1e9, start, end,
                                           interval["start"], interval["end"], *state])
                    for name, camera in robot.cameras.items():
                        image = result.get(name)
                        if image is not None:
                            self.record_image(name, camera, image, idx, end)
                except Exception as error:
                    self.record_error = str(error)
                    if self.phase == "control":
                        raise
                return result
            finally:
                self.local.observation = None
        self.replace(robot_class, "get_observation", observe)

        original_read = bus_class.sync_read

        @wraps(original_read)
        def read(bus, data_name, *args, **kwargs):
            item = getattr(self.local, "observation", None)
            started = time.perf_counter_ns()
            try:
                return original_read(bus, data_name, *args, **kwargs)
            finally:
                if item is not None and item["bus"] is bus and data_name == "Present_Position":
                    item["start"], item["end"] = started, time.perf_counter_ns()
        self.replace(bus_class, "sync_read", read)

        original_send = robot_class.send_action

        @wraps(original_send)
        def send(robot, action, *args, **kwargs):
            if self.phase == "control" and self.manual_stop.is_set():
                # 이미 진행 중인 전송을 취소하지는 못하지만 새 모델 목표 전송은 막는다.
                raise KeyboardInterrupt
            self.check_writer()
            if self.phase not in {"control", "shutdown"}:
                return original_send(robot, action, *args, **kwargs)
            idx = self.action_index
            self.action_index += 1
            start = time.perf_counter_ns()
            obs = self.latest_observation or ("", "", [""] * len(JOINTS))
            item = {"bus": robot.bus, "sent": {}, "write_start": "", "write_end": ""}
            self.local.action = item
            status, error_type = "error", ""
            try:
                result = original_send(robot, action, *args, **kwargs)
                status = "api_returned"
                return result
            except BaseException as error:
                error_type = type(error).__name__
                raise
            finally:
                end = time.perf_counter_ns()
                try:
                    self.action_csv.writerow([
                        idx, self.phase, obs[0], obs[1], (start - self.origin_ns) / 1e9, start, end,
                        item["write_start"], item["write_end"], status, error_type, *obs[2],
                        *positions(action), *positions(item["sent"])])
                    if status == "error":
                        self.flush()
                except Exception as error:
                    self.record_error = str(error)
                    # 종료 중 기록 실패나 원래 통신 예외를 다른 기록 예외로 덮지 않는다.
                    if self.phase == "control" and status != "error":
                        raise
                finally:
                    self.local.action = None
        self.replace(robot_class, "send_action", send)

        original_write = bus_class.sync_write

        @wraps(original_write)
        def write(bus, data_name, values, *args, **kwargs):
            item = getattr(self.local, "action", None)
            if item is None or item["bus"] is not bus or data_name != "Goal_Position":
                return original_write(bus, data_name, values, *args, **kwargs)
            item["sent"] = {f"{key}.pos": float(value) for key, value in values.items()}
            item["write_start"] = time.perf_counter_ns()
            try:
                return original_write(bus, data_name, values, *args, **kwargs)
            finally:
                item["write_end"] = time.perf_counter_ns()
        self.replace(bus_class, "sync_write", write)

        original_hardware = camera_class._read_from_hardware

        @wraps(original_hardware)
        def hardware(camera, *args, **kwargs):
            started = time.perf_counter_ns()
            result = original_hardware(camera, *args, **kwargs)
            self.local.capture = (id(camera), started, time.perf_counter_ns())
            return result
        self.replace(camera_class, "_read_from_hardware", hardware)
        original_process = camera_class._postprocess_image

        @wraps(original_process)
        def process(camera, *args, **kwargs):
            result = original_process(camera, *args, **kwargs)
            capture = getattr(self.local, "capture", None)
            if capture is not None and capture[0] == id(camera):
                with self.frame_lock:
                    key = (id(camera), id(result))
                    self.frames[key] = (weakref.ref(result), *capture[1:], time.perf_counter_ns())
                    self.frames.move_to_end(key)
                    while len(self.frames) > 256:
                        self.frames.popitem(last=False)
            return result
        self.replace(camera_class, "_postprocess_image", process)

    def record_image(self, name, camera, image, obs_index, end_ns):
        with self.frame_lock:
            entry = self.frames.get((id(camera), id(image)))
        matched = entry is not None and entry[0]() is image
        stamp = entry[2] if matched else None
        reused = matched and self.previous_frames.get(name) == stamp
        self.previous_frames[name] = stamp
        if not matched:
            self.unmatched_frames += 1
        color = plain(camera.config.color_mode)
        fps = camera.config.fps or 30
        queued = True
        try:
            self.video_jobs.put_nowait((name, image.copy(), obs_index, self.phase, end_ns, fps, color))
        except queue.Full:
            queued = False
            self.video_drops += 1
            if self.video_drops == 1:
                print("영상 기록 대기열이 찼습니다. 누락은 시간 로그에 표시하며 제어는 유지합니다.", file=sys.stderr)
        self.camera_csv.writerow([obs_index, self.phase, name, end_ns,
                                 entry[1] if matched else "", stamp if matched else "",
                                 entry[3] if matched else "", int(matched), int(reused), int(queued)])

    def close(self, outcome="completed", error_type=""):
        if self.closed:
            return
        self.closed = True
        if self.keys is not None:
            try:
                self.keys.stop()
            except Exception as error:
                self.record_error = f"수동 평가 입력 정리 실패: {error}"
        self.video_stop.set()
        deadline = time.monotonic() + 15
        while self.worker.is_alive() and time.monotonic() < deadline:
            try:
                self.video_jobs.put(None, timeout=0.1)
                break
            except queue.Full:
                continue
        self.worker.join(timeout=max(0, deadline - time.monotonic()))
        if self.worker.is_alive():
            self.video_error = RuntimeError("영상 정리 제한 시간 15초를 초과했습니다.")
        for owner, name, own, original in reversed(self.patches):
            if own:
                setattr(owner, name, original)
            else:
                delattr(owner, name)
        self.metadata.update({
            "status": outcome, "error_type": error_type,
            "observation_count": self.observation_index, "action_count": self.action_index,
            "video_frame_counts": self.video_counts, "video_queue_dropped_frames": self.video_drops,
            "unmatched_camera_timestamps": self.unmatched_frames,
            "video_error": str(self.video_error) if self.video_error else None,
            "record_error": self.record_error,
            "recording_complete": outcome == "completed" and self.observation_index > 0
                                  and self.video_error is None and self.record_error is None
                                  and not self.video_drops and not self.unmatched_frames,
            "end_monotonic_ns": time.perf_counter_ns(),
        })
        streams = self.streams[:-1] if self.worker.is_alive() else self.streams
        for stream in streams:
            try:
                stream.close()
            except OSError as error:
                self.record_error = str(error)
        self.metadata["record_error"] = self.record_error
        if self.record_error is not None:
            self.metadata["recording_complete"] = False
        if outcome == "error" and self.evaluation["outcome"] == "not_assessed":
            self.evaluation["termination_reason"] = "execution_error"
        self.evaluation.update({"execution_status": outcome, "execution_error_type": error_type,
                                "recording_complete": self.metadata["recording_complete"],
                                "task": self.metadata.get("task"), "policy_path": self.metadata.get("policy_path"),
                                "run_dir": str(self.path),
                                "keyboard_error": self.keys.error if self.keys else None})
        self.save_evaluation()
        self.metadata["evaluation"] = dict(self.evaluation)
        self.save_metadata()
        print(f"검증 기록 저장: {self.path}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", required=True)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--manual-evaluation", action="store_true")
    args, rest = parser.parse_known_args()
    if rest[:1] == ["--"]:
        rest = rest[1:]
    from lerobot.robots.omx_follower.omx_follower import OmxFollower
    from lerobot.motors.dynamixel.dynamixel import DynamixelMotorsBus
    from lerobot.cameras.opencv.camera_opencv import OpenCVCamera
    from lerobot.rollout.strategies.base import BaseStrategy
    from lerobot.scripts.lerobot_rollout import main as rollout_main

    record = RolloutRecord(args.log_dir, manual_evaluation=args.manual_evaluation)
    outcome, error_type = "completed", ""
    try:
        record.install(OmxFollower, DynamixelMotorsBus, OpenCVCamera, BaseStrategy)
        if args.check_only:
            outcome = "check_only"
            print("장치 접속·모델 로딩 없이 기록 훅을 확인했습니다.")
            return
        sys.argv = [sys.argv[0], *rest]
        rollout_main()
    except BaseException as error:
        outcome, error_type = "error", type(error).__name__
        raise
    finally:
        try:
            record.close(outcome, error_type)
        except Exception as error:
            if outcome != "error":
                raise
            print(f"원래 오류 처리 중 검증 기록 정리도 실패했습니다: {error}", file=sys.stderr)
    if record.video_error is not None or record.record_error is not None:
        raise RuntimeError("검증 기록 저장이 완료되지 않았습니다.") from record.video_error


if __name__ == "__main__":
    main()
