"""가상 관측·가상 명령·합성 영상으로 기록기를 검증하며 장치를 열지 않는다."""

import csv
import importlib.util
import json
import io
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("rollout_record", ROOT / "tools/record_omx_rollout.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FakeFrame:
    @staticmethod
    def from_ndarray(image, format):
        return NS(pts=None, time_base=None)


class FakeVideoStream:
    def __init__(self):
        self.codec_context = NS()

    def encode(self, frame=None):
        return []


class FakeContainer:
    def add_stream(self, *args, **kwargs):
        return FakeVideoStream()

    def mux(self, packet):
        pass

    def close(self):
        pass


FAKE_AV = NS(open=lambda *args, **kwargs: FakeContainer(), VideoFrame=FakeFrame)


def fake_classes():
    class Bus:
        def __init__(self):
            self.reads, self.writes = 0, 0
            self.motors = {j: {"id": i + 11} for i, j in enumerate(module.JOINTS)}
            self.calibration = {j: {"range_min": 0, "range_max": 4095} for j in module.JOINTS}
            self.fail = False

        def sync_read(self, data_name):
            self.reads += 1
            return {j: 1.0 for j in module.JOINTS}

        def sync_write(self, data_name, values):
            self.writes += 1
            if self.fail:
                raise ConnectionError("가상 전송 실패")

    class Camera:
        def __init__(self):
            self.config = NS(color_mode="rgb", fps=30, width=16, height=16, fourcc="YUYV",
                             rotation=0, index_or_path="가상 카메라")
            self.frame = None

        def _read_from_hardware(self):
            return np.zeros((16, 16, 3), dtype=np.uint8)

        def _postprocess_image(self, image):
            return image

        def produce(self):
            self.frame = self._postprocess_image(self._read_from_hardware())

    class Robot:
        def __init__(self):
            self.bus = Bus()
            self.cameras = {"top": Camera()}
            self.calibration = self.bus.calibration
            self.config = NS(id="가상 팔", port="가상 포트", use_degrees=False, max_relative_target=5)

        def get_observation(self):
            values = {f"{k}.pos": v for k, v in self.bus.sync_read("Present_Position").items()}
            values["top"] = self.cameras["top"].frame
            return values

        def send_action(self, action):
            self.bus.sync_read("Present_Position")
            safe = {k.removesuffix(".pos"): min(v, 6.0) for k, v in action.items()}
            self.bus.sync_write("Goal_Position", safe)
            return {f"{k}.pos": v for k, v in safe.items()}

    class Strategy:
        def run(self, ctx):
            return ctx.perform()

    return Robot, Bus, Camera, Strategy


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.record = module.RolloutRecord(self.path, av_module=FAKE_AV)
        self.addCleanup(self.record.close)
        self.Robot, self.Bus, self.Camera, self.Strategy = fake_classes()
        self.original = self.Robot.get_observation
        self.record.install(self.Robot, self.Bus, self.Camera, self.Strategy)
        self.robot = self.Robot()
        self.robot.cameras["top"].produce()

    def ctx(self, perform):
        cfg = NS(policy=NS(pretrained_path=self.path / "not_a_model", device="cpu", use_amp=False), task="가상 작업", fps=15, device="cpu",
                 inference={"type": "rtc"}, rename_map={"observation.images.top": "observation.images.camera1"},
                 return_to_initial_position=True)
        return NS(hardware=NS(robot_wrapper=NS(inner=self.robot), initial_position={}),
                  runtime=NS(cfg=cfg, shutdown_event=threading.Event()), perform=perform)

    def rows(self, name):
        with (self.path / name).open() as stream:
            return list(csv.DictReader(stream))

    def test_actual_requested_and_clipped_values_without_extra_io(self):
        requested = {f"{j}.pos": 20.0 for j in module.JOINTS}

        def perform():
            self.robot.get_observation()
            return self.robot.send_action(requested)

        result = self.Strategy().run(self.ctx(perform))
        self.record.close()
        self.assertEqual(self.robot.bus.reads, 2)
        self.assertEqual(self.robot.bus.writes, 1)
        self.assertEqual(result["elbow_flex.pos"], 6)
        self.assertEqual(requested["elbow_flex.pos"], 20)
        row = self.rows("actions.csv")[0]
        self.assertEqual(row["requested_elbow_flex"], "20.0")
        self.assertEqual(row["sent_elbow_flex"], "6.0")
        self.assertEqual(row["actual_elbow_flex"], "1.0")
        self.assertEqual(row["status"], "api_returned")
        self.assertEqual(row["observation_index"], "0")
        self.assertLessEqual(int(row["observation_end_ns"]), int(row["start_ns"]))
        self.assertIs(self.Robot.get_observation, self.original)
        meta = json.loads((self.path / "run_metadata.json").read_text())
        self.assertTrue(meta["recording_complete"])
        self.assertEqual(meta["success"], "not_assessed")

    def test_video_frame_is_linked_to_observation_and_shutdown_is_separate(self):
        self.Strategy().run(self.ctx(lambda: self.robot.get_observation()))
        self.robot.get_observation()
        self.record.close()
        self.assertEqual([r["phase"] for r in self.rows("observations.csv")], ["control", "shutdown"])
        cameras = self.rows("camera_timing.csv")
        self.assertEqual([r["matched_frame"] for r in cameras], ["1", "1"])
        self.assertEqual([r["reused_frame"] for r in cameras], ["0", "1"])
        video = self.rows("video_frames.csv")
        self.assertEqual([r["observation_index"] for r in video], ["0", "1"])
        self.assertEqual(video[0]["pts_us"], "0")
        self.assertGreater(int(video[1]["pts_us"]), 0)

    def test_error_propagates_and_pending_target_is_recorded(self):
        self.robot.bus.fail = True

        def perform():
            self.robot.get_observation()
            self.robot.send_action({f"{j}.pos": 20 for j in module.JOINTS})

        with self.assertRaises(ConnectionError):
            self.Strategy().run(self.ctx(perform))
        self.record.close("error", "ConnectionError")
        row = self.rows("actions.csv")[0]
        self.assertEqual(row["status"], "error")
        self.assertEqual(row["error_type"], "ConnectionError")
        self.assertEqual(row["sent_gripper"], "6.0")
        self.assertFalse(json.loads((self.path / "run_metadata.json").read_text())["recording_complete"])

    def test_full_video_queue_does_not_block_control(self):
        self.record.video_jobs.put(None)
        self.record.worker.join()
        self.record.video_jobs = module.queue.Queue(maxsize=1)
        self.record.video_jobs.put("대기열 포화")
        self.Strategy().run(self.ctx(lambda: self.robot.get_observation()))
        self.record.close()
        self.assertEqual(self.rows("camera_timing.csv")[0]["video_queued"], "0")
        meta = json.loads((self.path / "run_metadata.json").read_text())
        self.assertEqual(meta["video_queue_dropped_frames"], 1)
        self.assertFalse(meta["recording_complete"])

    def test_unmatched_timestamp_is_explicit_not_fabricated(self):
        self.record.frames.clear()
        self.Strategy().run(self.ctx(lambda: self.robot.get_observation()))
        self.record.close()
        row = self.rows("camera_timing.csv")[0]
        self.assertEqual(row["matched_frame"], "0")
        self.assertEqual(row["hardware_read_end_ns"], "")
        self.assertFalse(json.loads((self.path / "run_metadata.json").read_text())["recording_complete"])

    def test_existing_record_is_protected(self):
        with self.assertRaises(FileExistsError):
            module.RolloutRecord(self.path, av_module=FAKE_AV)

    def test_encoder_failure_propagates_after_marking_incomplete(self):
        self.record.video_jobs.put(None)
        self.record.worker.join()
        self.record.video_error = OSError("가상 디스크 오류")
        with self.assertRaisesRegex(RuntimeError, "영상 기록"):
            self.Strategy().run(self.ctx(lambda: self.robot.get_observation()))
        # 종료 단계에서는 기록 오류가 원래 하드웨어 정리 호출을 가로막지 않는다.
        self.robot.get_observation()
        self.record.close("error", "RuntimeError")
        self.assertFalse(json.loads((self.path / "run_metadata.json").read_text())["recording_complete"])

    def test_shutdown_recording_failure_never_blocks_original_calls(self):
        self.Strategy().run(self.ctx(lambda: self.robot.get_observation()))
        class BrokenCSV:
            def writerow(self, row):
                raise OSError("가상 디스크 가득 참")
        self.record.obs_csv = BrokenCSV()
        self.record.action_csv = BrokenCSV()
        self.record.last_flush = 0
        self.record.flush = lambda: (_ for _ in ()).throw(OSError("가상 flush 실패"))
        self.robot.get_observation()
        self.robot.send_action({f"{j}.pos": 20 for j in module.JOINTS})
        self.assertEqual(self.robot.bus.writes, 1)
        self.assertIsNotNone(self.record.record_error)

    def test_runtime_device_is_saved_separately(self):
        ctx = self.ctx(lambda: self.robot.get_observation())
        ctx.runtime.cfg.policy.device = "cuda"
        self.Strategy().run(ctx)
        self.record.close()
        meta = json.loads((self.path / "run_metadata.json").read_text())
        self.assertEqual(meta["execution_device"], "cpu")
        self.assertEqual(meta["policy_device"], "cuda")

    def test_stop_event_finishes_worker_without_sentinel(self):
        self.record.video_stop.set()
        self.record.worker.join(timeout=1)
        self.assertFalse(self.record.worker.is_alive())

    def test_success_stops_new_policy_action_but_allows_return_action(self):
        self.record.manual_evaluation = True
        action = {f"{j}.pos": 2 for j in module.JOINTS}
        ctx = self.ctx(None)
        def perform():
            self.robot.get_observation()
            self.robot.send_action(action)
            self.assertTrue(self.record.evaluate("s"))
            self.assertFalse(self.record.evaluate("f"))
            self.assertTrue(ctx.runtime.shutdown_event.is_set())
            self.robot.send_action(action)
        ctx.perform = perform
        with self.assertRaises(KeyboardInterrupt):
            self.Strategy().run(ctx)
        self.assertEqual(self.robot.bus.writes, 1)
        self.assertEqual(json.loads((self.path/"evaluation.json").read_text())["outcome"], "success")
        self.robot.send_action(action)
        self.assertEqual(self.robot.bus.writes, 2)
        self.record.close()
        result = json.loads((self.path/"evaluation.json").read_text())
        self.assertEqual(result["outcome"], "success")
        self.assertEqual(result["source"], "manual_keyboard")
        self.assertEqual(json.loads((self.path/"run_metadata.json").read_text())["success"], "success")

    def test_failure_and_unlabeled_quit_are_separate(self):
        self.record.manual_evaluation = True
        def perform():
            self.robot.get_observation()
            self.robot.send_action({f"{j}.pos": 2 for j in module.JOINTS})
            self.record.evaluate("f")
        self.Strategy().run(self.ctx(perform))
        self.record.close()
        self.assertEqual(json.loads((self.path/"evaluation.json").read_text())["outcome"], "failure")
        with tempfile.TemporaryDirectory() as temp:
            other = module.RolloutRecord(temp, av_module=FAKE_AV, manual_evaluation=True)
            try:
                other.phase = "control"
                other.action_index = 1
                other.control_start_ns = module.time.perf_counter_ns()
                other.shutdown_event = threading.Event()
                self.assertTrue(other.evaluate("q"))
                self.assertEqual(other.evaluation["outcome"], "not_assessed")
                self.assertEqual(other.evaluation["termination_reason"], "manual_quit")
            finally:
                other.close()

    def test_ctrl_c_style_shutdown_is_not_success(self):
        ctx = self.ctx(None)
        def perform():
            self.robot.get_observation()
            ctx.runtime.shutdown_event.set()
        ctx.perform = perform
        self.Strategy().run(ctx)
        self.record.close()
        result = json.loads((self.path/"evaluation.json").read_text())
        self.assertEqual(result["outcome"], "not_assessed")
        self.assertEqual(result["termination_reason"], "shutdown_requested_without_label")

    def test_premature_and_late_evaluation_ignored(self):
        self.record.manual_evaluation = True
        def perform():
            self.assertFalse(self.record.evaluate("s"))
            self.robot.get_observation()
        self.Strategy().run(self.ctx(perform))
        self.assertFalse(self.record.evaluate("f"))
        self.record.close()
        self.assertEqual(self.record.evaluation["outcome"], "not_assessed")

    def test_success_label_survives_later_cleanup_error(self):
        self.record.manual_evaluation = True
        def perform():
            self.robot.get_observation()
            self.robot.send_action({f"{j}.pos": 2 for j in module.JOINTS})
            self.record.evaluate("s")
        self.Strategy().run(self.ctx(perform))
        self.record.close("error", "ConnectionError")
        result = json.loads((self.path/"evaluation.json").read_text())
        self.assertEqual(result["outcome"], "success")
        self.assertEqual(result["execution_status"], "error")
        self.assertFalse(result["recording_complete"])

    def test_keyboard_start_exception_enters_shutdown_before_return(self):
        self.record.manual_evaluation = True
        with patch.object(module,"TerminalEvaluation") as factory:
            factory.return_value.start.side_effect = RuntimeError("가상 입력 스레드 시작 실패")
            with self.assertRaises(RuntimeError):
                self.Strategy().run(self.ctx(lambda: self.fail("실행되면 안 됩니다.")))
            self.assertEqual(self.record.phase,"shutdown")
            self.record.video_error = OSError("가상 저장 오류")
            self.robot.send_action({f"{j}.pos": 2 for j in module.JOINTS})
            self.assertEqual(self.robot.bus.writes,1)
            factory.return_value.error = None
            self.record.close("error","RuntimeError")

    def test_constructor_failure_closes_all_opened_csv(self):
        created = []
        class BrokenRecord(module.RolloutRecord):
            def __new__(cls,*args,**kwargs):
                result = super().__new__(cls)
                created.append(result)
                return result
            def save_evaluation(self):
                raise OSError("가상 초기 판정 파일 저장 실패")
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(OSError):
                BrokenRecord(temp,av_module=FAKE_AV)
            self.assertTrue(all(s.closed for s in created[0].streams))
            created[0].close()

    def test_terminal_restore_error_does_not_skip_recording_cleanup(self):
        import termios
        keys = module.TerminalEvaluation(lambda key: None,io.StringIO())
        keys.original_mode = [0]*7
        keys.fd = 0
        self.record.keys = keys
        with patch.object(termios,"tcsetattr",side_effect=termios.error("가상 TTY 복원 실패")):
            self.record.close()
        self.assertFalse(self.record.worker.is_alive())
        self.assertTrue(all(s.closed for s in self.record.streams))
        result = json.loads((self.path/"evaluation.json").read_text())
        self.assertIsNotNone(result["keyboard_error"])


class KeyboardTests(unittest.TestCase):
    def test_non_tty_is_supported_without_keyboard(self):
        keyboard = module.TerminalEvaluation(lambda key: self.fail("비TTY 입력을 읽으면 안 됩니다."), io.StringIO())
        self.assertFalse(keyboard.start())
        keyboard.stop()

    @unittest.skipUnless(os.name == "posix", "TTY 테스트는 POSIX에서 실행합니다.")
    def test_pty_keys_escape_filter_and_terminal_restore(self):
        import pty
        import termios
        master, slave = pty.openpty()
        stream = os.fdopen(os.dup(slave), "r")
        before = termios.tcgetattr(slave)
        keys, received = [], threading.Event()
        def callback(key):
            keys.append(key)
            received.set()
        keyboard = module.TerminalEvaluation(callback, stream)
        try:
            with patch.object(os,"tcgetpgrp",return_value=os.getpgrp()):
                self.assertTrue(keyboard.start())
            during = termios.tcgetattr(slave)
            self.assertFalse(during[3] & termios.ICANON)
            self.assertEqual(during[3] & termios.ISIG, before[3] & termios.ISIG)
            os.write(master,b"\x1b[Sf")
            self.assertTrue(received.wait(2))
            keyboard.stop()
            self.assertEqual(keys,["f"])
            self.assertEqual(termios.tcgetattr(slave),before)
            self.assertFalse(keyboard.thread.is_alive())
        finally:
            keyboard.stop()
            stream.close()
            os.close(master)
            os.close(slave)

    @unittest.skipUnless(os.name == "posix", "TTY 테스트는 POSIX에서 실행합니다.")
    def test_thread_start_failure_restores_terminal(self):
        import pty
        import termios
        master,slave = pty.openpty()
        stream = os.fdopen(os.dup(slave),"r")
        before = termios.tcgetattr(slave)
        keys = module.TerminalEvaluation(lambda key: None,stream)
        try:
            with patch.object(os,"tcgetpgrp",return_value=os.getpgrp()), \
                    patch.object(threading.Thread,"start",side_effect=RuntimeError("가상 스레드 실패")):
                self.assertFalse(keys.start())
            self.assertEqual(termios.tcgetattr(slave),before)
        finally:
            keys.stop()
            stream.close()
            os.close(master)
            os.close(slave)


class RealVideoTests(unittest.TestCase):
    def test_synthetic_rgb_video_decodes_with_irregular_timestamps(self):
        try:
            import av
        except ImportError:
            self.skipTest("PyAV가 있는 Docker 환경에서 합성 영상 인코딩을 검증합니다.")
        with tempfile.TemporaryDirectory() as temp:
            record = module.RolloutRecord(temp, av_module=av)
            self.addCleanup(record.close)
            Robot, Bus, Camera, Strategy = fake_classes()
            record.install(Robot, Bus, Camera, Strategy)
            camera = Camera()
            camera.produce()
            camera.frame[:, :, 0] = 255
            record.phase = "control"
            for index, offset in enumerate([0, 40_000_000, 120_000_000]):
                record.record_image("top", camera, camera.frame, index, record.origin_ns + offset)
            record.close()
            with av.open(str(Path(temp) / "videos/top.mp4")) as video:
                frames = list(video.decode(video=0))
            self.assertEqual(len(frames), 3)
            for frame, expected in zip(frames, [0, 0.04, 0.12]):
                self.assertAlmostEqual(float(frame.pts * frame.time_base), expected, places=3)
            pixel = frames[0].to_ndarray(format="rgb24")[0, 0]
            self.assertGreater(int(pixel[0]), 240)
            self.assertLess(int(pixel[2]), 10)
            self.assertEqual((Path(temp) / "videos/top.mp4").stat().st_mode & 0o444, 0o444)


if __name__ == "__main__":
    unittest.main()
