"""실제 장치 없이 진단 래퍼의 오류 전달과 원래 동작 보존을 검사한다."""

import csv
import json
import os
import importlib.util
from pathlib import Path
import tempfile
import subprocess
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("rollout_diagnostics", ROOT / "tools/diagnose_omx_rollout.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RolloutDiagnosticsTests(unittest.TestCase):
    def test_late_success_is_saved_as_success_without_changing_packet(self):
        class Port:
            packet_timeout = 100

            def readPort(self, length):
                return b""

            def writePort(self, packet):
                return len(packet)

        result = ([255, 255, 253, 0, 16], 0)

        class Handler:
            def rxPacket(self, port, fast_option):
                return result

        with tempfile.TemporaryDirectory() as directory:
            profiler = MODULE.RolloutProfiler(directory)
            profiler.trace_packets(Handler, Port)
            with patch.object(MODULE.time, "perf_counter", side_effect=[1.0, 1.04, 1.041]):
                self.assertIs(Handler().rxPacket(Port(), False), result)
            profiler.close()
            row = json.loads((Path(directory) / "packet_errors.jsonl").read_text())
            self.assertEqual(row["trigger"], "slow_success")
            self.assertEqual(row["result"], 0)
            self.assertAlmostEqual(row["history"][-1]["elapsed_ms"], 40)

    def test_rx_timeout_extension_keeps_original_start_and_never_shortens_timeout(self):
        class Port:
            def setPacketTimeout(self, length):
                self.packet_start_time = 123
                self.packet_timeout = float(length)
                return "original"

        with tempfile.TemporaryDirectory() as directory:
            profiler = MODULE.RolloutProfiler(directory)
            profiler.extend_rx_timeout(Port, 100)
            port = Port()
            self.assertEqual(port.setPacketTimeout(34), "original")
            self.assertEqual(port.packet_timeout, 100)
            self.assertEqual(port.packet_start_time, 123)
            port.setPacketTimeout(150)
            self.assertEqual(port.packet_timeout, 150)
            profiler.close()

    def test_packet_trace_preserves_io_and_saves_original_bytes_on_error(self):
        payload = bytes.fromhex("fffffd0010")
        returned = ([255, 255, 253, 0, 16], -3002)
        written = []

        class Port:
            ser = types.SimpleNamespace(in_waiting=5)

            def readPort(self, length):
                return payload

            def writePort(self, packet):
                written.append(packet)
                return len(packet)

        class Handler:
            def rxPacket(self, port, fast_option):
                self.received = port.readPort(20)
                return returned

        original_read = Port.readPort
        with tempfile.TemporaryDirectory() as directory:
            profiler = MODULE.RolloutProfiler(directory)
            profiler.trace_packets(Handler, Port)
            port = Port()
            packet = [255, 255, 253, 0, 16, 7]
            self.assertEqual(port.writePort(packet), len(packet))
            handler = Handler()
            self.assertIs(handler.rxPacket(port, False), returned)
            self.assertIs(handler.received, payload)
            self.assertIs(written[0], packet)
            report = json.loads((Path(directory) / "packet_errors.jsonl").read_text())
            self.assertEqual(report["result"], -3002)
            reads = [x for x in report["history"] if x["event"] == "port_rx_bytes"]
            self.assertEqual(reads[0]["hex"], payload.hex())
            self.assertEqual(report["history"][0]["pending_rx_bytes"], 5)
            profiler.close()
            self.assertIs(Port.readPort, original_read)

    def test_successful_packet_does_not_create_error_file(self):
        class Port:
            def readPort(self, length):
                return b""

            def writePort(self, packet):
                return len(packet)

        class Handler:
            def rxPacket(self, port, fast_option):
                return [255, 255, 253, 0, 16], 0

        with tempfile.TemporaryDirectory() as directory:
            profiler = MODULE.RolloutProfiler(directory)
            profiler.trace_packets(Handler, Port)
            Handler().rxPacket(Port(), False)
            profiler.close()
            self.assertFalse((Path(directory) / "packet_errors.jsonl").exists())

    def test_frozen_gripper_keeps_other_model_targets_and_retains_gripper_transmission(self):
        sent = []

        class Bus:
            def _sync_write(self, *args, **kwargs):
                pass

        class Robot:
            def get_observation(self):
                return {"gripper.pos": 49.0, "elbow_flex.pos": 20.0}

            def send_action(self, action):
                sent.append(dict(action))
                return action

        with tempfile.TemporaryDirectory() as directory:
            profiler = MODULE.RolloutProfiler(directory)
            profiler.freeze_gripper(Robot)
            profiler.trace_actions(Robot, Bus)
            robot = Robot()
            robot.get_observation()
            target = {"gripper.pos": 40.0, "elbow_flex.pos": 10.0}
            self.assertEqual(robot.send_action(target), {"gripper.pos": 49.0, "elbow_flex.pos": 10.0})
            self.assertEqual(target["gripper.pos"], 40.0)
            self.assertEqual(sent, [{"gripper.pos": 49.0, "elbow_flex.pos": 10.0}])
            profiler.close()
            rows = [json.loads(line) for line in (Path(directory) / "actions.jsonl").read_text().splitlines()]
            self.assertEqual(rows[0]["target"]["gripper.pos"], 40.0)
            self.assertEqual(rows[1]["returned"]["gripper.pos"], 49.0)

    def test_runner_automatically_saves_console_and_keeps_failed_exit_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / "fake_python"
            fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\necho stub-run\nexit 7\n')
            fake.chmod(0o700)
            env = dict(os.environ, OMX_PYTHON=str(fake), OMX_LOG_ROOT=str(root / "logs"),
                       OMX_DIAG_DURATION="3")
            result = subprocess.run(["bash", str(ROOT / "tools/run_omx_diagnosis.sh"),
                                     "--position-read-mode", "individual", "--freeze-gripper"],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 7)
            logs = list((root / "logs").glob("*/console.log"))
            self.assertEqual(len(logs), 1)
            console = logs[0].read_text()
            self.assertIn("--freeze-gripper", console)
            self.assertIn("--duration=3", console)
            self.assertIn("stub-run", console)

    def test_individual_reads_retry_whole_sweep_without_reusing_partial_values(self):
        calls = []

        class Bus:
            motors = {"a": types.SimpleNamespace(id=11), "b": types.SimpleNamespace(id=16)}

            def _get_motors_list(self, motors):
                return list(self.motors)

            def sync_read(self, *args, **kwargs):
                raise AssertionError("그룹 조회를 호출하면 안 됩니다.")

            def read(self, data_name, name, **kwargs):
                calls.append((name, kwargs))
                if len(calls) == 2:
                    raise ConnectionError("첫 묶음 실패")
                return float(len(calls))

        with tempfile.TemporaryDirectory() as directory:
            profiler = MODULE.RolloutProfiler(directory)
            profiler.individual_position_reads(Bus)
            result = Bus().sync_read("Present_Position", normalize=False, num_retry=1)
            self.assertEqual(result, {"a": 3.0, "b": 4.0})
            self.assertEqual([name for name, _ in calls], ["a", "b", "a", "b"])
            self.assertTrue(all(options == {"normalize": False, "num_retry": 0} for _, options in calls))
            profiler.close()

    def test_individual_reads_stop_on_final_error_and_keep_other_register_reads(self):
        failure = ConnectionError("조회 실패")
        calls = []

        class Bus:
            motors = {"a": types.SimpleNamespace(id=11)}

            def _get_motors_list(self, motors):
                return ["a"]

            def sync_read(self, *args, **kwargs):
                calls.append((args, kwargs))
                return {"a": 4}

            def read(self, *args, **kwargs):
                raise failure

        with tempfile.TemporaryDirectory() as directory:
            profiler = MODULE.RolloutProfiler(directory)
            profiler.individual_position_reads(Bus)
            self.assertEqual(Bus().sync_read("Operating_Mode", normalize=False), {"a": 4})
            self.assertEqual(len(calls), 1)
            with self.assertRaises(ConnectionError) as caught:
                Bus().sync_read("Present_Position", num_retry=1)
            self.assertIs(caught.exception, failure)
            profiler.close()

    def test_read_retry_is_delegated_only_for_position_without_overriding_explicit_value(self):
        calls = []

        class Bus:
            def sync_read(self, data_name, *args, **kwargs):
                calls.append((data_name, kwargs))
                return {"wrist_flex": 12.0}

        with tempfile.TemporaryDirectory() as directory:
            profiler = MODULE.RolloutProfiler(directory)
            profiler.retry_position_reads(Bus, 1)
            bus = Bus()
            self.assertEqual(bus.sync_read("Present_Position"), {"wrist_flex": 12.0})
            bus.sync_read("Present_Position", num_retry=0)
            bus.sync_read("Operating_Mode")
            self.assertEqual(calls, [("Present_Position", {"num_retry": 1}),
                                     ("Present_Position", {"num_retry": 0}), ("Operating_Mode", {})])
            profiler.close()

    def test_read_retry_does_not_suppress_final_failure(self):
        failure = ConnectionError("재조회도 실패")

        class Bus:
            def sync_read(self, data_name, **kwargs):
                raise failure

        with tempfile.TemporaryDirectory() as directory:
            profiler = MODULE.RolloutProfiler(directory)
            profiler.retry_position_reads(Bus, 1)
            with self.assertRaises(ConnectionError) as caught:
                Bus().sync_read("Present_Position")
            self.assertIs(caught.exception, failure)
            profiler.close()

    def test_read_recovery_marker_requires_successful_return(self):
        with tempfile.TemporaryDirectory() as directory:
            profiler = MODULE.RolloutProfiler(directory)

            class Bus:
                def sync_read(self, data_name, **kwargs):
                    profiler.rx_comm_failures += 1
                    return {"wrist_flex": 12.0}

            profiler.retry_position_reads(Bus, 1)
            self.assertEqual(Bus().sync_read("Present_Position"), {"wrist_flex": 12.0})
            profiler.close()
            with profiler.events_path.open(encoding="utf-8") as stream:
                self.assertEqual(next(csv.DictReader(stream))["operation"], "read_recovered")

    def test_action_trace_records_request_before_failure_without_changing_values(self):
        failure = ConnectionError("원래 전송 실패")
        seen = []

        class Bus:
            def _sync_write(self, addr, length, values):
                seen.append((addr, length, values))
                raise failure

        class Robot:
            def __init__(self):
                self.bus = Bus()

            def get_observation(self):
                return {"wrist_flex.pos": 50.0, "camera": object()}

            def send_action(self, action):
                self.bus._sync_write(116, 4, {14: 2048})

        with tempfile.TemporaryDirectory() as directory:
            profiler = MODULE.RolloutProfiler(directory)
            profiler.trace_actions(Robot, Bus)
            robot = Robot()
            robot.get_observation()
            action = {"wrist_flex.pos": 30.0}
            with self.assertRaises(ConnectionError) as caught:
                robot.send_action(action)
            self.assertIs(caught.exception, failure)
            self.assertEqual(action, {"wrist_flex.pos": 30.0})
            self.assertEqual(seen, [(116, 4, {14: 2048})])
            rows = [json.loads(line) for line in (Path(directory) / "actions.jsonl").read_text().splitlines()]
            self.assertEqual(rows[0]["delta_to_state"], {"wrist_flex.pos": -20.0})
            self.assertEqual(rows[1]["values"], {"14": 2048})
            self.assertNotIn("camera", rows[0]["state"])
            self.assertEqual([row["event"] for row in rows], ["action_requested", "raw_goal_write_requested"])
            profiler.close()

    def test_suppressed_action_trace_does_not_write_to_bus(self):
        Robot, Bus, calls = self.inference_fakes()
        Robot.get_observation = lambda robot: {"wrist_flex.pos": 50.0}
        with tempfile.TemporaryDirectory() as directory:
            profiler = MODULE.RolloutProfiler(directory)
            profiler.inference_only(Robot, Bus)
            profiler.trace_actions(Robot, Bus)
            robot = Robot()
            robot.get_observation()
            self.assertEqual(robot.send_action({"wrist_flex.pos": 30.0}), {"wrist_flex.pos": 30.0})
            self.assertEqual(calls, [])
            profiler.close()
            rows = [json.loads(line) for line in (Path(directory) / "actions.jsonl").read_text().splitlines()]
            self.assertEqual([row["event"] for row in rows], ["action_requested", "action_api_returned"])

    def test_hold_mode_keeps_first_observation_and_ignores_model_targets(self):
        initial = {f"joint{i}.pos": float(i) for i in range(6)}
        observations = iter([initial, {key: value + 1 for key, value in initial.items()}])
        sent = []

        class Robot:
            def get_observation(self):
                return dict(next(observations))

            def send_action(self, action):
                sent.append(dict(action))
                return action

        with tempfile.TemporaryDirectory() as directory:
            profiler = MODULE.RolloutProfiler(directory)
            profiler.hold_initial_position(Robot)
            robot = Robot()
            robot.get_observation()
            robot.send_action({key: 99 for key in initial})
            robot.get_observation()
            robot.send_action({key: -99 for key in initial})
            self.assertEqual(sent, [initial, initial])
            profiler.close()
            self.assertTrue((Path(directory) / "hold_target.json").exists())

    def test_hold_mode_fails_before_sending_without_valid_observation(self):
        sent = []

        class Robot:
            def get_observation(self):
                return {"joint1.pos": float("nan")}

            def send_action(self, action):
                sent.append(action)

        with tempfile.TemporaryDirectory() as directory:
            profiler = MODULE.RolloutProfiler(directory)
            profiler.hold_initial_position(Robot)
            robot = Robot()
            with self.assertRaises(ValueError):
                robot.send_action({"joint1.pos": 0})
            with self.assertRaises(ValueError):
                robot.get_observation()
            self.assertEqual(sent, [])
            profiler.close()

    def test_configured_mode_preserves_setup_but_blocks_goal_register(self):
        Robot, Bus, calls = self.inference_fakes()
        original_connect = Robot.connect
        original_disconnect = Robot.disconnect
        with tempfile.TemporaryDirectory() as directory:
            profiler = MODULE.RolloutProfiler(directory)
            profiler.configured_inference_only(Robot, Bus)
            robot = Robot()
            robot.connect()
            robot.bus._write(11, 1, 14, 4)
            self.assertEqual(robot.send_action({"wrist_flex.pos": 20}), {"wrist_flex.pos": 20})
            with self.assertRaises(RuntimeError):
                robot.bus._write(116, 4, 14, 2048)
            with self.assertRaises(RuntimeError):
                robot.bus._sync_write(116, 4, {14: 2048})
            self.assertEqual(calls, ["original_connect", "write"])
            self.assertIs(Robot.connect, original_connect)
            self.assertIs(Robot.disconnect, original_disconnect)
            profiler.close()

    def test_configured_mode_keeps_original_communication_exception(self):
        failure = ConnectionError("설정 응답 실패")

        class Bus:
            def _write(self, addr, *args, **kwargs):
                raise failure

            def _sync_write(self, addr, *args, **kwargs):
                raise failure

        class Robot:
            def send_action(self, action):
                raise AssertionError("모델 목표값 전송이 호출되면 안 됩니다.")

        with tempfile.TemporaryDirectory() as directory:
            profiler = MODULE.RolloutProfiler(directory)
            profiler.configured_inference_only(Robot, Bus)
            with self.assertRaises(ConnectionError) as caught:
                Bus()._write(64, 1, 14, 0)
            self.assertIs(caught.exception, failure)
            profiler.close()
            with profiler.events_path.open(encoding="utf-8") as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(row["operation"], "register_addr_64")
            self.assertEqual(row["error_type"], "ConnectionError")

    def inference_fakes(self, calibrated=True):
        calls = []

        class Bus:
            is_connected = False

            def connect(self):
                calls.append("connect")
                self.is_connected = True

            def disconnect(self, disable_torque=True):
                calls.append(("disconnect", disable_torque))
                self.is_connected = False

            def _write(self, *args, **kwargs):
                calls.append("write")

            def _sync_write(self, *args, **kwargs):
                calls.append("sync_write")

        class Robot:
            def __init__(self):
                self.bus = Bus()
                self.is_calibrated = calibrated
                self.cameras = {}

            def connect(self):
                calls.append("original_connect")

            def disconnect(self):
                calls.append("original_disconnect")

            def send_action(self, action):
                self.bus._sync_write(action)

        return Robot, Bus, calls

    def test_inference_only_never_sends_action_or_torque_writes(self):
        Robot, Bus, calls = self.inference_fakes()
        original_connect = Robot.connect
        with tempfile.TemporaryDirectory() as directory:
            profiler = MODULE.RolloutProfiler(directory)
            profiler.inference_only(Robot, Bus)
            robot = Robot()
            robot.connect()
            action = {"wrist_flex.pos": 20.0}
            self.assertEqual(robot.send_action(action), action)
            with self.assertRaises(RuntimeError):
                robot.bus._write("Torque_Enable", 1)
            with self.assertRaises(RuntimeError):
                robot.bus._sync_write("Goal_Position", 20)
            robot.disconnect()
            self.assertEqual(calls, ["connect", ("disconnect", False)])
            profiler.close()
            self.assertIs(Robot.connect, original_connect)
            with profiler.events_path.open(encoding="utf-8") as stream:
                self.assertEqual(next(csv.DictReader(stream))["operation"], "action_suppressed")

    def test_inference_only_calibration_mismatch_fails_without_writes(self):
        Robot, Bus, calls = self.inference_fakes(calibrated=False)
        with tempfile.TemporaryDirectory() as directory:
            profiler = MODULE.RolloutProfiler(directory)
            profiler.inference_only(Robot, Bus)
            with self.assertRaisesRegex(ValueError, "보정값"):
                Robot().connect()
            self.assertEqual(calls, ["connect", ("disconnect", False)])
            profiler.close()

    def test_inference_only_closes_connected_camera_on_error(self):
        Robot, Bus, calls = self.inference_fakes()
        camera_calls = []
        good_camera = types.SimpleNamespace(is_connected=False)

        def connect_camera():
            good_camera.is_connected = True

        def disconnect_camera():
            camera_calls.append("close")
            good_camera.is_connected = False

        def fail_camera():
            raise ConnectionError("카메라 연결 실패")

        good_camera.connect = connect_camera
        good_camera.disconnect = disconnect_camera
        bad_camera = types.SimpleNamespace(is_connected=False, connect=fail_camera)
        with tempfile.TemporaryDirectory() as directory:
            profiler = MODULE.RolloutProfiler(directory)
            profiler.inference_only(Robot, Bus)
            robot = Robot()
            robot.cameras = {"first": good_camera, "second": bad_camera}
            with self.assertRaises(ConnectionError):
                robot.connect()
            self.assertEqual(camera_calls, ["close"])
            self.assertEqual(calls, ["connect", ("disconnect", False)])
            profiler.close()

    def test_original_exception_and_inheritance_are_preserved(self):
        failure = ConnectionError("모터 응답 없음")

        class Parent:
            def read(self):
                raise failure

        class Child(Parent):
            pass

        with tempfile.TemporaryDirectory() as directory:
            profiler = MODULE.RolloutProfiler(directory)
            profiler.time_method(Child, "read", "motor_read")
            with self.assertRaises(ConnectionError) as caught:
                Child().read()
            self.assertIs(caught.exception, failure)
            profiler.close()
            self.assertNotIn("read", Child.__dict__)
            with profiler.events_path.open(encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0]["status"], "error")
            self.assertEqual(rows[0]["error_type"], "ConnectionError")

    def test_chunk_and_cached_calls_are_distinguished_without_changing_values(self):
        class Policy:
            def __init__(self):
                self.pending = []

            def _get_action_chunk(self):
                return [0.1, 0.2]

            def get_action(self):
                if not self.pending:
                    self.pending.extend(self._get_action_chunk())
                return self.pending.pop(0)

        with tempfile.TemporaryDirectory() as directory:
            profiler = MODULE.RolloutProfiler(directory)
            profiler.mark_chunks(Policy)
            profiler.time_method(Policy, "get_action", "policy_pipeline")
            policy = Policy()
            self.assertEqual([policy.get_action() for _ in range(3)], [0.1, 0.2, 0.1])
            profiler.close()
            with profiler.events_path.open(encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual([row["operation"] for row in rows],
                             ["policy_chunk", "policy_cached", "policy_chunk"])

    def test_control_phase_is_closed_even_when_the_loop_fails(self):
        class Strategy:
            def run(self):
                raise RuntimeError("원래 실행 오류")

        original = Strategy.run
        with tempfile.TemporaryDirectory() as directory:
            profiler = MODULE.RolloutProfiler(directory)
            profiler.mark_control(Strategy)
            with self.assertRaisesRegex(RuntimeError, "원래 실행 오류"):
                Strategy().run()
            self.assertEqual(profiler.phase, "shutdown")
            profiler.close()
            self.assertIs(Strategy.run, original)

    def test_existing_logs_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            profiler = MODULE.RolloutProfiler(directory)
            profiler.close()
            before = profiler.events_path.read_bytes()
            with self.assertRaises(FileExistsError):
                MODULE.RolloutProfiler(directory)
            self.assertEqual(profiler.events_path.read_bytes(), before)

    def test_sdk_error_return_is_recorded_without_retry_or_conversion(self):
        response = ([], -3001, 0)

        class Handler:
            def __init__(self):
                self.calls = 0

            def readRx(self, port, dxl_id, length):
                self.calls += 1
                return response

        original = Handler.readRx
        with tempfile.TemporaryDirectory() as directory:
            profiler = MODULE.RolloutProfiler(directory)
            profiler.trace_read_rx(Handler)
            handler = Handler()
            self.assertIs(handler.readRx(None, 14, 4), response)
            self.assertEqual(handler.calls, 1)
            profiler.close()
            self.assertIs(Handler.readRx, original)
            with profiler.events_path.open(encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0]["operation"], "motor_rx_id_14")
            self.assertEqual(rows[0]["status"], "error")
            self.assertEqual(rows[0]["error_type"], "COMM_-3001")

    def test_packet_status_is_recorded_without_overriding_sdk_success(self):
        response = ([1, 2, 3, 4], 0, 128)

        class Handler:
            def readRx(self, port, dxl_id, length):
                return response

        with tempfile.TemporaryDirectory() as directory:
            profiler = MODULE.RolloutProfiler(directory)
            profiler.trace_read_rx(Handler)
            self.assertIs(Handler().readRx(None, 12, 4), response)
            profiler.close()
            with profiler.events_path.open(encoding="utf-8") as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(row["error_type"], "PACKET_STATUS_0x80")


if __name__ == "__main__":
    unittest.main()
