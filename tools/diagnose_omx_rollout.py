#!/usr/bin/env python3
"""LeRobot 동기 추론의 구간별 시간을 기록하며 원래 오류 처리를 유지한다."""

import argparse
from collections import defaultdict, deque
import csv
import json
from functools import wraps
import math
from pathlib import Path
import statistics
import sys
import time


FIELDS = ["phase", "tick", "time_sec", "operation", "duration_ms", "status", "error_type"]


class RolloutProfiler:
    """센서나 명령값을 바꾸지 않고 호출 시간과 오류 종류만 저장한다."""

    def __init__(self, log_dir):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.log_dir / "timing.csv"
        self.summary_path = self.log_dir / "timing_summary.csv"
        if self.summary_path.exists():
            raise FileExistsError("기존 보고서를 보호합니다. 새 로그 폴더를 지정하세요.")
        self.stream = self.events_path.open("x", newline="", encoding="utf-8")
        self.writer = csv.DictWriter(self.stream, fieldnames=FIELDS)
        self.writer.writeheader()
        self.origin = time.perf_counter()
        self.last_flush = self.origin
        self.phase = "startup"
        self.tick = 0
        self.previous_tick = None
        self.chunk_requests = 0
        self.samples = defaultdict(list)
        self.errors = defaultdict(int)
        self.patches = []
        self.action_stream = None
        self.rx_comm_failures = 0
        self.packet_stream = None

    def record(self, operation, duration_ms, status="ok", error_type=""):
        now = time.perf_counter()
        self.writer.writerow(dict(zip(FIELDS, [
            self.phase, self.tick, now - self.origin, operation,
            duration_ms, status, error_type,
        ])))
        key = (self.phase, operation)
        self.samples[key].append(duration_ms)
        self.errors[key] += int(status != "ok")
        if status != "ok" or now - self.last_flush >= 1:
            self.stream.flush()
            self.last_flush = now

    def replace(self, owner, name, replacement):
        # 상속받은 메서드도 원래 속성 구조로 복원할 수 있도록 기록한다.
        own_attribute = name in owner.__dict__
        original = getattr(owner, name)
        self.patches.append((owner, name, original, own_attribute))
        setattr(owner, name, replacement)

    def before_observation(self):
        if self.phase != "control":
            return
        now = time.perf_counter()
        self.tick += 1
        if self.previous_tick is not None:
            self.record("control_interval", (now - self.previous_tick) * 1000)
        self.previous_tick = now

    def time_method(self, owner, name, operation, *, observation=False):
        original = getattr(owner, name)

        @wraps(original)
        def measured(*args, **kwargs):
            if observation:
                self.before_observation()
            started = time.perf_counter()
            chunks_before = self.chunk_requests
            error_type = ""
            try:
                return original(*args, **kwargs)
            except BaseException as error:
                error_type = type(error).__name__
                raise
            finally:
                label = operation
                if operation == "policy_pipeline":
                    label = "policy_chunk" if self.chunk_requests > chunks_before else "policy_cached"
                self.record(label, (time.perf_counter() - started) * 1000,
                            "error" if error_type else "ok", error_type)

        self.replace(owner, name, measured)

    def mark_chunks(self, policy_class):
        original = policy_class._get_action_chunk

        @wraps(original)
        def marked(*args, **kwargs):
            # GPU 동기화를 추가하지 않고 새 예측을 요청한 횟수만 센다.
            self.chunk_requests += 1
            return original(*args, **kwargs)

        self.replace(policy_class, "_get_action_chunk", marked)

    def mark_control(self, strategy_class):
        original = strategy_class.run

        @wraps(original)
        def marked(*args, **kwargs):
            self.phase = "control"
            self.tick = 0
            self.previous_tick = None
            try:
                return original(*args, **kwargs)
            finally:
                self.phase = "shutdown"

        self.replace(strategy_class, "run", marked)

    def trace_read_rx(self, handler_class):
        """그룹 읽기에서 기다리던 ID와 SDK 반환 코드를 원래 값 그대로 기록한다."""
        original = handler_class.readRx

        @wraps(original)
        def traced(handler, port, dxl_id, length):
            started = time.perf_counter()
            error_type = ""
            try:
                response = original(handler, port, dxl_id, length)
                _, result, packet_status = response
                if result != 0:
                    self.rx_comm_failures += 1
                    error_type = f"COMM_{result}"
                elif packet_status != 0:
                    error_type = f"PACKET_STATUS_0x{packet_status:02x}"
                return response
            except BaseException as error:
                error_type = type(error).__name__
                raise
            finally:
                self.record(f"motor_rx_id_{dxl_id}", (time.perf_counter() - started) * 1000,
                            "error" if error_type else "ok", error_type)

        self.replace(handler_class, "readRx", traced)

    def retry_position_reads(self, bus_class, retries):
        """SDK의 제한된 재조회만 사용하고 최종 실패 예외는 유지한다."""
        original = bus_class.sync_read

        @wraps(original)
        def read(bus, data_name, *args, **kwargs):
            before = self.rx_comm_failures
            if data_name == "Present_Position":
                kwargs.setdefault("num_retry", retries)
            result = original(bus, data_name, *args, **kwargs)
            if data_name == "Present_Position" and self.rx_comm_failures > before:
                self.record("read_recovered", 0)
            return result

        self.replace(bus_class, "sync_read", read)

    def individual_position_reads(self, bus_class):
        """위치 조회만 개별 요청으로 바꾸되 전체 관절 묶음의 재시도 횟수는 유지한다."""
        original = bus_class.sync_read

        @wraps(original)
        def read(bus, data_name, motors=None, *, normalize=True, num_retry=0):
            if data_name != "Present_Position":
                return original(bus, data_name, motors, normalize=normalize, num_retry=num_retry)
            names = bus._get_motors_list(motors)
            for attempt in range(num_retry + 1):
                values = {}
                try:
                    for name in names:
                        started = time.perf_counter()
                        error_type = ""
                        try:
                            values[name] = bus.read(data_name, name, normalize=normalize, num_retry=0)
                        except ConnectionError as error:
                            self.rx_comm_failures += 1
                            error_type = type(error).__name__
                            raise
                        finally:
                            self.record(f"individual_read_id_{bus.motors[name].id}",
                                        (time.perf_counter() - started) * 1000,
                                        "error" if error_type else "ok", error_type)
                    return values
                except ConnectionError:
                    if attempt == num_retry:
                        raise
                    self.record("individual_sweep_retry", 0)

        self.replace(bus_class, "sync_read", read)

    def inference_only(self, robot_class, bus_class):
        """기존 보정값을 확인해 읽기 연결하고 모든 레지스터 쓰기를 차단한다."""
        def blocked_write(*args, **kwargs):
            raise RuntimeError("추론 전용 검사에서는 모터 레지스터 쓰기를 허용하지 않습니다.")

        def disconnect(robot):
            try:
                for camera in robot.cameras.values():
                    if camera.is_connected:
                        camera.disconnect()
            finally:
                if robot.bus.is_connected:
                    robot.bus.disconnect(disable_torque=False)

        def connect(robot, calibrate=True):
            try:
                robot.bus.connect()
                if not robot.is_calibrated:
                    raise ValueError("보정값이 일치하지 않습니다. 추론 전용 검사에서는 보정값을 쓰지 않습니다.")
                for camera in robot.cameras.values():
                    camera.connect()
            except BaseException:
                disconnect(robot)
                raise

        def suppress_action(robot, action):
            self.record("action_suppressed", 0)
            return dict(action)

        self.replace(bus_class, "_write", blocked_write)
        self.replace(bus_class, "_sync_write", blocked_write)
        self.replace(robot_class, "connect", connect)
        self.replace(robot_class, "disconnect", disconnect)
        self.replace(robot_class, "send_action", suppress_action)

    def configured_inference_only(self, robot_class, bus_class):
        """일반 초기화는 유지하고 목표 위치 레지스터 쓰기만 차단한다."""
        def suppress_action(robot, action):
            self.record("action_suppressed", 0)
            return dict(action)

        def guard_write(name):
            original = getattr(bus_class, name)

            @wraps(original)
            def guarded(bus, addr, *args, **kwargs):
                # 현재 OMX 팔로워의 모든 모델에서 Goal_Position은 주소 116이다.
                if addr == 116:
                    self.record("goal_write_blocked", 0, "error", "RuntimeError")
                    raise RuntimeError("초기화 비교 검사에서는 목표 위치 쓰기를 차단합니다.")
                started = time.perf_counter()
                error_type = ""
                try:
                    return original(bus, addr, *args, **kwargs)
                except BaseException as error:
                    error_type = type(error).__name__
                    raise
                finally:
                    self.record(f"register_addr_{addr}", (time.perf_counter() - started) * 1000,
                                "error" if error_type else "ok", error_type)

            self.replace(bus_class, name, guarded)

        guard_write("_write")
        guard_write("_sync_write")
        self.replace(robot_class, "send_action", suppress_action)

    def hold_initial_position(self, robot_class):
        """일반 초기화 후 처음 읽은 위치를 고정 목표로 반복 전송한다."""
        original_observe = robot_class.get_observation
        original_send = robot_class.send_action
        target = None

        @wraps(original_observe)
        def observe(robot, *args, **kwargs):
            nonlocal target
            observation = original_observe(robot, *args, **kwargs)
            if target is None:
                candidate = {key: float(value) for key, value in observation.items() if key.endswith(".pos")}
                if len(candidate) != 6 or not all(math.isfinite(value) for value in candidate.values()):
                    raise ValueError("초기 위치 6개를 정상적으로 읽지 못해 목표값을 전송하지 않습니다.")
                with (self.log_dir / "hold_target.json").open("x", encoding="utf-8") as stream:
                    json.dump(candidate, stream, ensure_ascii=False, indent=2)
                target = candidate
            return observation

        @wraps(original_send)
        def send(robot, action):
            if target is None or set(action) != set(target):
                raise ValueError("초기 위치와 명령 관절 이름이 일치하지 않아 전송을 중단합니다.")
            self.record("hold_action", 0)
            return original_send(robot, dict(target))

        self.replace(robot_class, "get_observation", observe)
        self.replace(robot_class, "send_action", send)

    def trace_actions(self, robot_class, bus_class):
        """예측값·직전 관측·SDK 목표값을 변경하지 않고 전송 요청 전에 저장한다."""
        original_observe = robot_class.get_observation
        original_send = robot_class.send_action
        original_write = bus_class._sync_write
        latest_state = {}
        previous_action = {}

        def save(event, **values):
            if self.action_stream is None:
                self.action_stream = (self.log_dir / "actions.jsonl").open("x", encoding="utf-8")
            row = dict(event=event, phase=self.phase, tick=self.tick,
                       time_sec=time.perf_counter() - self.origin, **values)
            self.action_stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            self.action_stream.flush()

        @wraps(original_observe)
        def observe(robot, *args, **kwargs):
            result = original_observe(robot, *args, **kwargs)
            latest_state.clear()
            latest_state.update({key: float(value) for key, value in result.items() if key.endswith(".pos")})
            return result

        @wraps(original_send)
        def send(robot, action):
            candidate = {key: float(value) for key, value in action.items()}
            save("action_requested", state=dict(latest_state), target=candidate,
                 delta_to_state={key: value - latest_state[key] for key, value in candidate.items()
                                 if key in latest_state},
                 delta_to_previous={key: value - previous_action[key] for key, value in candidate.items()
                                    if key in previous_action})
            previous_action.clear()
            previous_action.update(candidate)
            result = original_send(robot, action)
            save("action_api_returned", returned={key: float(value) for key, value in result.items()})
            return result

        @wraps(original_write)
        def write(bus, addr, length, ids_values, *args, **kwargs):
            if addr == 116:
                save("raw_goal_write_requested", values={str(key): int(value) for key, value in ids_values.items()})
            return original_write(bus, addr, length, ids_values, *args, **kwargs)

        self.replace(robot_class, "get_observation", observe)
        self.replace(robot_class, "send_action", send)
        self.replace(bus_class, "_sync_write", write)

    def freeze_gripper(self, robot_class):
        """집게 위치만 첫 관측값으로 고정하고 나머지 목표와 통신은 유지한다."""
        original_observe = robot_class.get_observation
        original_send = robot_class.send_action
        target = None

        @wraps(original_observe)
        def observe(robot, *args, **kwargs):
            nonlocal target
            result = original_observe(robot, *args, **kwargs)
            if target is None:
                value = float(result["gripper.pos"])
                if not math.isfinite(value):
                    raise ValueError("집게 초기값이 유효하지 않아 전송을 중단합니다.")
                target = value
                with (self.log_dir / "frozen_gripper.json").open("x", encoding="utf-8") as stream:
                    json.dump({"gripper.pos": target}, stream)
            return result

        @wraps(original_send)
        def send(robot, action):
            if target is None or "gripper.pos" not in action:
                raise ValueError("집게 초기값 또는 목표가 없어 전송을 중단합니다.")
            modified = dict(action)
            modified["gripper.pos"] = target
            self.record("gripper_target_frozen", 0)
            return original_send(robot, modified)

        self.replace(robot_class, "get_observation", observe)
        self.replace(robot_class, "send_action", send)

    def trace_packets(self, handler_class, port_class):
        """최근 바이트 기록을 메모리에 보관하고 SDK 수신 실패 시에만 저장한다."""
        history = deque(maxlen=128)
        original_read = port_class.readPort
        original_write = port_class.writePort
        original_rx = handler_class.rxPacket

        def remember(event, **values):
            history.append(dict(event=event, phase=self.phase, tick=self.tick,
                                time_sec=time.perf_counter() - self.origin, **values))

        @wraps(original_read)
        def read(port, length):
            result = original_read(port, length)
            if result:
                data = bytes(result)
                remember("port_rx_bytes", length=len(data), hex=data[:256].hex(), truncated=len(data) > 256)
            return result

        @wraps(original_write)
        def write(port, packet):
            data = bytes(packet)
            serial = getattr(port, "ser", None)
            try:
                pending = getattr(serial, "in_waiting", None)
            except Exception:
                pending = None
            remember("port_tx_request", length=len(data), hex=data[:256].hex(),
                     truncated=len(data) > 256, pending_rx_bytes=pending)
            result = original_write(port, packet)
            remember("port_tx_return", written=result)
            return result

        @wraps(original_rx)
        def receive(handler, port, *args, **kwargs):
            started = time.perf_counter()
            timeout_ms = getattr(port, "packet_timeout", None)
            result = original_rx(handler, port, *args, **kwargs)
            packet, code = result
            elapsed_ms = (time.perf_counter() - started) * 1000
            remember("sdk_rx_result", result=code, packet_id=packet[4] if len(packet) > 4 else None,
                     sdk_packet_hex=bytes(packet[:256]).hex(), elapsed_ms=elapsed_ms,
                     timeout_ms_at_start=timeout_ms)
            if code != 0 or elapsed_ms >= 35:
                if self.packet_stream is None:
                    self.packet_stream = (self.log_dir / "packet_errors.jsonl").open("x", encoding="utf-8")
                self.packet_stream.write(json.dumps(dict(result=code, trigger="error" if code else "slow_success",
                                                        history=list(history)),
                                                   ensure_ascii=False) + "\n")
                self.packet_stream.flush()
            return result

        self.replace(port_class, "readPort", read)
        self.replace(port_class, "writePort", write)
        self.replace(handler_class, "rxPacket", receive)

    def extend_rx_timeout(self, port_class, timeout_ms):
        """요청 내용이나 SDK 판정을 변경하지 않고 최소 응답 대기만 늘린다."""
        original = port_class.setPacketTimeout

        @wraps(original)
        def set_timeout(port, packet_length):
            result = original(port, packet_length)
            port.packet_timeout = max(port.packet_timeout, timeout_ms)
            return result

        self.replace(port_class, "setPacketTimeout", set_timeout)

    def close(self):
        for owner, name, original, own_attribute in reversed(self.patches):
            if own_attribute:
                setattr(owner, name, original)
            else:
                delattr(owner, name)
        self.patches.clear()
        self.stream.close()
        if self.action_stream is not None:
            self.action_stream.close()
        if self.packet_stream is not None:
            self.packet_stream.close()
        columns = ["phase", "operation", "count", "errors", "mean_ms", "p95_ms", "max_ms"]
        with self.summary_path.open("x", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=columns)
            writer.writeheader()
            for (phase, operation), values in sorted(self.samples.items()):
                ordered = sorted(values)
                writer.writerow(dict(zip(columns, [
                    phase, operation, len(values), self.errors[(phase, operation)],
                    statistics.fmean(values), ordered[math.ceil(len(values) * 0.95) - 1], max(values),
                ])))


def install_hooks(profiler, inference_only=False, configured_inference_only=False,
                  hold_initial_position=False, read_retries=0, position_read_mode="group",
                  freeze_gripper=False, trace_packets=False, rx_timeout_ms=None):
    # 원래 CLI의 설정 클래스 등록을 유지하며 여기서는 하드웨어에 연결하지 않는다.
    from lerobot.scripts import lerobot_rollout
    from lerobot.cameras.opencv.camera_opencv import OpenCVCamera
    from lerobot.motors.dynamixel.dynamixel import DynamixelMotorsBus
    from lerobot.policies.smolvla.modeling_smolvla import SmolVLAPolicy
    from lerobot.robots.omx_follower.omx_follower import OmxFollower
    from lerobot.rollout.inference.sync import SyncInferenceEngine
    from lerobot.rollout.strategies.base import BaseStrategy
    from dynamixel_sdk.protocol2_packet_handler import Protocol2PacketHandler
    from dynamixel_sdk import PortHandler

    profiler.mark_control(BaseStrategy)
    profiler.mark_chunks(SmolVLAPolicy)
    profiler.trace_read_rx(Protocol2PacketHandler)
    if rx_timeout_ms is not None:
        profiler.extend_rx_timeout(PortHandler, rx_timeout_ms)
    if trace_packets:
        profiler.trace_packets(Protocol2PacketHandler, PortHandler)
    if position_read_mode == "individual":
        profiler.individual_position_reads(DynamixelMotorsBus)
    profiler.time_method(DynamixelMotorsBus, "sync_read", "motor_read")
    profiler.time_method(DynamixelMotorsBus, "sync_write", "motor_write")
    profiler.time_method(OpenCVCamera, "read_latest", "camera_latest")
    profiler.time_method(OmxFollower, "get_observation", "observation", observation=True)
    profiler.time_method(OmxFollower, "send_action", "send_action")
    profiler.time_method(SyncInferenceEngine, "get_action", "policy_pipeline")
    if inference_only:
        profiler.inference_only(OmxFollower, DynamixelMotorsBus)
    elif configured_inference_only:
        profiler.configured_inference_only(OmxFollower, DynamixelMotorsBus)
    elif hold_initial_position:
        profiler.hold_initial_position(OmxFollower)
    if freeze_gripper:
        profiler.freeze_gripper(OmxFollower)
    profiler.trace_actions(OmxFollower, DynamixelMotorsBus)
    if read_retries:
        profiler.retry_position_reads(DynamixelMotorsBus, read_retries)
    return lerobot_rollout.main


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", required=True, help="타이밍 CSV를 저장할 새 폴더")
    parser.add_argument("--check-only", action="store_true", help="연결·추론 없이 계측 코드만 검사")
    parser.add_argument("--read-retries", type=int, choices=(0, 1, 2), default=0,
                        help="관절 위치 조회의 추가 시도 횟수. 기본 0, 최종 실패는 그대로 중단")
    parser.add_argument("--position-read-mode", choices=("group", "individual"), default="group",
                        help="관절 위치 읽기 방식. 기본 그룹 조회 또는 ID별 개별 조회")
    parser.add_argument("--freeze-gripper", action="store_true", help="집게 목표만 시작값에 고정, 읽기·쓰기는 유지")
    parser.add_argument("--trace-packets", action="store_true", help="SDK 수신 실패 직전의 송수신 바이트 기록 저장")
    parser.add_argument("--rx-timeout-ms", type=float, help="SDK 응답 대기 최소값(ms). 미지정 시 원래 계산 유지")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--inference-only", action="store_true", help="모터 설정·목표값 쓰기 없이 관측과 추론만 검사")
    modes.add_argument("--configured-inference-only", action="store_true",
                       help="일반 모터 초기화는 실행하고 목표값 쓰기는 차단")
    modes.add_argument("--hold-initial-position", action="store_true",
                       help="추론은 유지하되 처음 읽은 관절 위치를 고정 목표로 실제 전송")
    parser.add_argument("rollout_args", nargs=argparse.REMAINDER, help="-- 뒤에 원래 rollout 옵션 입력")
    args = parser.parse_args()
    if args.rx_timeout_ms is not None and not (0 < args.rx_timeout_ms <= 200):
        parser.error("수신 대기 시간은 0 초과 200 ms 이하로 지정하세요.")
    forwarded = args.rollout_args
    if forwarded[:1] == ["--"]:
        forwarded = forwarded[1:]
    if not args.check_only and "--inference.type=sync" not in forwarded:
        parser.error("진단 대상은 동기 실행입니다. -- 뒤에 --inference.type=sync를 지정하세요.")
    if (args.inference_only or args.configured_inference_only or args.hold_initial_position) and not args.check_only:
        if "--strategy.type=base" not in forwarded or "--return_to_initial_position=false" not in forwarded:
            parser.error("추론 전용 검사는 --strategy.type=base와 --return_to_initial_position=false가 필요합니다.")

    profiler = RolloutProfiler(args.log_dir)
    previous_argv = sys.argv
    try:
        run_rollout = install_hooks(profiler, inference_only=args.inference_only,
                                    configured_inference_only=args.configured_inference_only,
                                    hold_initial_position=args.hold_initial_position,
                                    read_retries=args.read_retries,
                                    position_read_mode=args.position_read_mode,
                                    freeze_gripper=args.freeze_gripper,
                                    trace_packets=args.trace_packets,
                                    rx_timeout_ms=args.rx_timeout_ms)
        if args.check_only:
            print("계측 코드 검사 통과: 로봇 연결과 모델 추론을 실행하지 않았습니다.", flush=True)
            return
        if args.inference_only:
            print("추론 전용 검사: 관측과 모델 계산만 수행하며 목표값·토크·모터 설정 쓰기를 차단합니다.", flush=True)
        elif args.configured_inference_only:
            print("초기화 비교 검사: 일반 모터 설정과 토크 활성화를 실행하되 목표 위치 쓰기는 차단합니다.", flush=True)
            print("초기화 시 기존 목표값과 토크 상태에 따라 움직일 수 있습니다.", flush=True)
        elif args.hold_initial_position:
            print("고정 목표 비교 검사: 일반 초기화 후 처음 읽은 위치를 반복 전송합니다.", flush=True)
            print("모델 계산은 계속하지만 예측한 이동 목표는 사용하지 않습니다. 위치 유지 명령은 실제 전송합니다.", flush=True)
        else:
            print("실제 팔로워를 움직이는 진단 실행입니다. 원래 통신 오류 처리를 유지합니다.", flush=True)
        print(f"관절 위치 조회 추가 시도: {args.read_retries}회. 최종 실패 시 중단합니다.", flush=True)
        print(f"관절 위치 읽기 방식: {args.position_read_mode}", flush=True)
        if args.freeze_gripper:
            print("집게 목표값만 시작값으로 고정합니다. 나머지 팔은 모델 명령으로 움직입니다.", flush=True)
        if args.trace_packets:
            print("수신 실패 시 packet_errors.jsonl에 최근 SDK 송수신 기록을 저장합니다.", flush=True)
        if args.rx_timeout_ms is not None:
            print(f"SDK 응답 최소 대기: {args.rx_timeout_ms} ms. 응답 없으면 최종 실패로 중단합니다.", flush=True)
        sys.argv = ["lerobot-rollout", *forwarded]
        run_rollout()
    finally:
        sys.argv = previous_argv
        profiler.close()
        print(f"상세 타이밍: {profiler.events_path}", flush=True)
        print(f"타이밍 요약: {profiler.summary_path}", flush=True)


if __name__ == "__main__":
    main()
