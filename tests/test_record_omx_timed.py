"""실제 장치 없이 시간 연결과 원래 동작 보존을 검증한다."""

import importlib.util
import json
from pathlib import Path
import threading
import tempfile
import unittest


spec = importlib.util.spec_from_file_location(
    "record_omx_timed", Path(__file__).resolve().parents[1] / "tools/record_omx_timed.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TimingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.profiler = module.RecordTiming(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.profiler.close)

        class Frame:
            pass

        class Camera:
            def _read_from_hardware(self):
                return Frame()

            def _postprocess_image(self, frame):
                return frame

            def async_read(self):
                return self.frame

            def read_latest(self):
                return self.frame

        class Bus:
            def sync_read(self):
                return {"joint": 2}

        class Robot:
            def __init__(self):
                self.cameras = {"wrist": Camera()}
                self.bus = Bus()

            def get_observation(self):
                images = {name: cam.read_latest() for name, cam in self.cameras.items()}
                return {**self.bus.sync_read(), "image": images["wrist"]}

            def send_action(self, value):
                return value

        class Writer:
            episode_buffer = None

        class Dataset:
            num_episodes = 0
            root = "/test/dataset"

            def __init__(self):
                self.writer = Writer()

            def add_frame(self, frame):
                if self.writer.episode_buffer is None:
                    self.writer.episode_buffer = {"size": 0}
                self.writer.episode_buffer["size"] += 1

            def save_episode(self):
                self.num_episodes += 1
                self.writer.episode_buffer = None

        self.robot_class, self.bus_class = Robot, Bus
        self.original = Robot.get_observation
        self.profiler.install(Robot, Bus, Camera, Dataset)
        self.robot = Robot()
        self.dataset = Dataset()

    def rows(self):
        return [json.loads(line) for line in
                (Path(self.temp.name) / "record_timing.jsonl").read_text().splitlines()]

    def observe(self):
        camera = self.robot.cameras["wrist"]
        camera.frame = camera._postprocess_image(camera._read_from_hardware())
        return self.robot.get_observation()

    def test_same_frame_times_and_return_values(self):
        observation = self.observe()
        self.assertEqual(observation["joint"], 2)
        action = {"joint": 3}
        self.bus_class().sync_read()
        self.assertIs(self.robot.send_action(action), action)
        self.dataset.add_frame({})
        self.dataset.save_episode()
        row = self.rows()[1]
        self.assertTrue(row["cameras"]["wrist"]["matched_frame"])
        self.assertLessEqual(row["cameras"]["wrist"]["hardware_read_end_ns"],
                             row["observation_start_ns"])
        self.assertEqual(len(row["bus_read_intervals_ns"]), 1)
        self.assertEqual(len(row["action_send_intervals_ns"]), 1)
        self.assertEqual(self.rows()[2]["attempt"], row["attempt"])

    def test_rerecord_same_episode_has_distinct_attempt(self):
        self.observe()
        self.dataset.add_frame({})
        self.dataset.writer.episode_buffer = None
        self.observe()
        self.dataset.add_frame({})
        a, b = self.rows()[1:]
        self.assertEqual(a["episode_index"], b["episode_index"])
        self.assertNotEqual(a["attempt"], b["attempt"])

    def test_async_camera_path_also_traced(self):
        observation = self.observe()
        self.assertIs(self.robot.cameras["wrist"].async_read(), observation["image"])
        self.dataset.add_frame({})
        self.assertTrue(self.rows()[1]["cameras"]["wrist"]["matched_frame"])

    def test_two_cameras_from_distinct_background_threads(self):
        camera_class = type(self.robot.cameras["wrist"])
        self.robot.cameras["top"] = camera_class()

        def capture(camera):
            camera.frame = camera._postprocess_image(camera._read_from_hardware())

        workers = [threading.Thread(target=capture, args=(cam,))
                   for cam in self.robot.cameras.values()]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
        self.robot.get_observation()
        self.dataset.add_frame({})
        cameras = self.rows()[1]["cameras"]
        self.assertEqual(set(cameras), {"wrist", "top"})
        self.assertTrue(all(cam["matched_frame"] for cam in cameras.values()))

    def test_refreshed_frame_key_is_not_evicted_as_old(self):
        """객체 ID 재사용처럼 기존 키가 갱신돼도 최신 시각을 보존한다."""
        camera = self.robot.cameras["wrist"]
        retained = []
        for _ in range(256):
            retained.append(camera._postprocess_image(camera._read_from_hardware()))
        # 같은 키에 새 수신 시각을 기록한 후 다른 프레임을 추가한다.
        camera.frame = camera._postprocess_image(retained[0])
        refreshed = self.profiler.frames[(id(camera), id(camera.frame))][2]
        retained.append(camera._postprocess_image(camera._read_from_hardware()))
        self.robot.get_observation()
        self.dataset.add_frame({})
        timing = self.rows()[1]["cameras"]["wrist"]
        self.assertTrue(timing["matched_frame"])
        self.assertEqual(timing["hardware_read_end_ns"], refreshed)

    def test_error_preserved_and_not_marked_saved(self):
        def fail():
            raise ConnectionError("원래 오류")
        self.robot.cameras["wrist"].read_latest = fail
        with self.assertRaisesRegex(ConnectionError, "원래 오류"):
            self.robot.get_observation()
        self.assertEqual(self.rows()[-1]["event"], "observation_error")

    def test_restore_methods(self):
        self.profiler.close()
        self.assertIs(self.robot_class.get_observation, self.original)

    def test_missing_camera_timing_stops_before_buffering(self):
        self.observe()
        self.profiler.local.row["cameras"].clear()
        with self.assertRaisesRegex(RuntimeError, "수신 시각 연결"):
            self.dataset.add_frame({})
        self.assertIsNone(self.dataset.writer.episode_buffer)
        self.assertEqual(self.rows()[-1]["event"], "frame_timing_error")

    def test_video_permissions_only_in_recorded_dataset(self):
        root = Path(self.temp.name) / "dataset"
        videos = root / "videos"
        videos.mkdir(parents=True)
        movie = videos / "test.mp4"
        movie.write_bytes(b"test")
        movie.chmod(0o600)
        other = Path(self.temp.name) / "other.mp4"
        other.write_bytes(b"other")
        other.chmod(0o600)
        (videos / "outside.mp4").symlink_to(other)
        self.profiler.dataset_roots.add(root)
        self.profiler.allow_video_reading()
        self.assertEqual(movie.stat().st_mode & 0o777, 0o600)
        self.profiler.readable_videos = True
        self.profiler.allow_video_reading()
        self.assertEqual(movie.stat().st_mode & 0o777, 0o644)
        self.assertEqual(other.stat().st_mode & 0o777, 0o600)

    @unittest.skipUnless(importlib.util.find_spec("lerobot"), "설치된 LeRobot 환경에서 추가 검증")
    def test_installed_camera_background_loop_and_read_latest(self):
        """실제 드라이버 코드에 합성 프레임을 공급하며 장치는 열지 않는다."""
        import numpy as np
        from lerobot.cameras.opencv.camera_opencv import OpenCVCamera
        from lerobot.cameras.configs import ColorMode

        self.profiler.close()

        class Camera(OpenCVCamera):
            @property
            def is_connected(self):
                return True

        camera = Camera.__new__(Camera)
        camera.stop_event = threading.Event()
        camera.new_frame_event = threading.Event()
        camera.frame_lock = threading.Lock()
        camera.capture_height, camera.capture_width = 2, 3
        camera.color_mode = ColorMode.RGB
        camera.rotation = -1
        camera.latest_frame = camera.latest_timestamp = None

        class Capture:
            def read(self):
                camera.stop_event.set()
                return True, np.zeros((2, 3, 3), dtype=np.uint8)

        class Alive:
            def is_alive(self):
                return True

        camera.videocapture = Capture()
        camera.thread = Alive()
        self.profiler = module.RecordTiming(Path(self.temp.name) / "installed")
        self.addCleanup(self.profiler.close)
        self.profiler.install(self.robot_class, self.bus_class, Camera, type(self.dataset))
        self.robot.cameras = {"wrist": camera}
        worker = threading.Thread(target=camera._read_loop)
        worker.start()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        observation = self.robot.get_observation()
        self.dataset.add_frame({})
        self.assertEqual(observation["image"].shape, (2, 3, 3))
        row = json.loads((Path(self.temp.name) / "installed/record_timing.jsonl").read_text().splitlines()[1])
        self.assertTrue(row["cameras"]["wrist"]["matched_frame"])
        self.assertGreater(row["cameras"]["wrist"]["hardware_read_end_ns"], 0)


if __name__ == "__main__":
    unittest.main()
