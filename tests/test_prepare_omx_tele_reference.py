"""저장 시도 선택과 실측 시간 변환을 가상 데이터로 검증한다."""

import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("tele_reference", ROOT / "tools/prepare_omx_tele_reference.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TimingTests(unittest.TestCase):
    def test_discarded_attempt_and_other_root_are_excluded(self):
        events = [
            {"event": "buffered_frame", "dataset_root": "/workspace/demo", "attempt": "discarded",
             "episode_index": 0, "frame_index": 0, "bus_read_intervals_ns": [[1, 2]]},
            {"event": "buffered_frame", "dataset_root": "/workspace/demo", "attempt": "saved",
             "episode_index": 0, "frame_index": 0, "bus_read_intervals_ns": [[100, 120]]},
            {"event": "saved_episode", "dataset_root": "/workspace/demo", "attempt": "saved", "episode_index": 0},
            {"event": "saved_episode", "dataset_root": "/workspace/another", "attempt": "other", "episode_index": 0},
        ]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "timing.jsonl"
            path.write_text("\n".join(json.dumps(e) for e in events))
            self.assertEqual(module.saved_timings(path, "demo"), {(0, 0): (100, 120)})
            events.append(events[2])
            path.write_text("\n".join(json.dumps(e) for e in events))
            with self.assertRaises(ValueError):
                module.saved_timings(path, "demo")

    def test_same_basename_from_different_roots_is_rejected(self):
        events = [
            {"event": "buffered_frame", "dataset_root": "/wrong/demo", "attempt": "saved",
             "episode_index": 0, "frame_index": 0, "bus_read_intervals_ns": [[1, 2]]},
            {"event": "saved_episode", "dataset_root": "/expected/demo", "attempt": "saved", "episode_index": 0},
        ]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "timing.jsonl"
            path.write_text("\n".join(json.dumps(e) for e in events))
            with self.assertRaises(ValueError):
                module.saved_timings(path, "demo")


class ExportTests(unittest.TestCase):
    def setUp(self):
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            self.skipTest("PyArrow가 있는 Docker 환경에서 Parquet 내보내기를 확인합니다.")
        self.pa, self.pq = pa, pq
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "demo"
        (self.root / "meta").mkdir(parents=True)
        (self.root / "data/chunk-000").mkdir(parents=True)
        self.output = Path(self.temp.name) / "reference"
        names = [f"{j}.pos" for j in reversed(module.JOINTS)]
        info = {"total_episodes": 1, "total_frames": 3, "fps": 30,
                "features": {key: {"names": names} for key in ["action", "observation.state"]}}
        (self.root / "meta/info.json").write_text(json.dumps(info))
        pq.write_table(pa.Table.from_pylist([{"task_index": 0, "task": "가상 작업"}]), self.root / "meta/tasks.parquet")
        self.rows = [{"episode_index": 0, "frame_index": i, "timestamp": i / 30, "task_index": 0,
                      "observation.state": [float(j) for j in reversed(range(6))],
                      "action": [float(j + 10) for j in reversed(range(6))]} for i in range(3)]
        pq.write_table(pa.Table.from_pylist(self.rows), self.root / "data/chunk-000/file-000.parquet")
        self.events = [{"event": "buffered_frame", "dataset_root": "/workspace/demo", "attempt": "saved",
                        "episode_index": 0, "frame_index": i,
                        "bus_read_intervals_ns": [[1_000_000_000+t, 1_002_000_000+t]]}
                       for i, t in enumerate([0, 40_000_000, 100_000_000])]
        self.events.append({"event": "saved_episode", "dataset_root": "/workspace/demo", "attempt": "saved", "episode_index": 0})
        self.timing = Path(self.temp.name) / "timing.jsonl"
        self.write_timing()

    def write_timing(self):
        self.timing.write_text("\n".join(json.dumps(e) for e in self.events))

    def test_midpoint_time_and_named_joint_order_preserved(self):
        result = module.export_reference(self.root, self.timing, self.output)
        with (self.output / "tele_reference.csv").open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(result["frames"], 3)
        self.assertEqual([float(r["time_sec"]) for r in rows], [0, 0.04, 0.1])
        self.assertEqual(float(rows[0]["state_shoulder_pan"]), 0)
        self.assertEqual(float(rows[0]["action_gripper"]), 15)
        self.assertEqual(float(rows[1]["dataset_timestamp_sec"]), 1/30)
        with self.assertRaises(FileExistsError):
            module.export_reference(self.root, self.timing, self.output)

    def test_missing_timing_does_not_leave_partial_output(self):
        self.events.pop(1)
        self.write_timing()
        with self.assertRaises(ValueError):
            module.export_reference(self.root, self.timing, self.output)
        self.assertFalse(self.output.exists())

    def test_nonfinite_joint_does_not_leave_partial_output(self):
        self.rows[1]["action"][0] = float("nan")
        self.pq.write_table(self.pa.Table.from_pylist(self.rows), self.root / "data/chunk-000/file-000.parquet")
        with self.assertRaises(ValueError):
            module.export_reference(self.root, self.timing, self.output)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
