"""실제 Parquet와 MP4를 만들어 읽기 전용 검사 및 CLI 결과를 검증한다."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

try:
    import av
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:
    av = None


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/check_omx_dataset.py"
SPEC = importlib.util.spec_from_file_location("quality_integration", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CAMERA = "observation.images.wrist"
NAMES = ["joint1", "joint2", "joint3", "joint4", "joint5", "gripper_joint_1"]


@unittest.skipIf(av is None, "실제 파일 검사는 Docker LeRobot 환경의 PyArrow/AV/NumPy가 필요합니다.")
class DatasetFileIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name) / "dataset"
        self.data = self.root / "data/chunk-000/file-000.parquet"
        self.meta = self.root / "meta/episodes/chunk-000/file-000.parquet"
        self.tasks = self.root / "meta/tasks.parquet"
        self.video = self.root / f"videos/{CAMERA}/chunk-000/file-000.mp4"
        for path in (self.data, self.meta, self.tasks, self.video):
            path.parent.mkdir(parents=True, exist_ok=True)
        info = {
            "codebase_version": "v3.0", "fps": 30, "total_episodes": 2, "total_frames": 6,
            "total_tasks": 1,
            "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
            "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
            "features": {**{key: {"shape": [6], "names": NAMES} for key in ("observation.state", "action")},
                         CAMERA: {"dtype": "video", "shape": [16, 16, 3]}},
        }
        (self.root / "meta/info.json").write_text(json.dumps(info), encoding="utf-8")
        self.rows = [{"episode_index": episode, "frame_index": i, "timestamp": i / 30,
                      "index": 3 * episode + i, "task_index": 0,
                      "observation.state": [0.1] * 6, "action": [0.2] * 6}
                     for episode in range(2) for i in range(3)]
        self.schema = pa.schema([(key, pa.int64()) for key in ("episode_index", "frame_index", "index", "task_index")]
                                + [("timestamp", pa.float32()), ("observation.state", pa.list_(pa.float32(), 6)),
                                   ("action", pa.list_(pa.float32(), 6))])
        self.write_data()
        prefix = f"videos/{CAMERA}"
        metadata = [{"episode_index": episode, "length": 3, "tasks": ["Pick up the cube"],
                     "dataset_from_index": 3 * episode, "dataset_to_index": 3 * (episode + 1),
                     "data/chunk_index": 0, "data/file_index": 0,
                     f"{prefix}/chunk_index": 0, f"{prefix}/file_index": 0,
                     f"{prefix}/from_timestamp": episode / 10, f"{prefix}/to_timestamp": (episode + 1) / 10}
                    for episode in range(2)]
        pq.write_table(pa.Table.from_pylist(metadata), self.meta)
        pq.write_table(pa.Table.from_pylist([{"task_index": 0, "task": "Pick up the cube"}]), self.tasks)
        with av.open(str(self.video), "w") as container:
            stream = container.add_stream("mpeg4", rate=30)
            stream.width = stream.height = 16
            stream.pix_fmt = "yuv420p"
            for i in range(6):
                frame = av.VideoFrame.from_ndarray(np.full((16, 16, 3), i * 20, dtype=np.uint8), format="rgb24")
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)

    def write_data(self):
        pq.write_table(pa.Table.from_pylist(self.rows, schema=self.schema), self.data)

    def cli(self, *extra):
        return subprocess.run([sys.executable, str(SCRIPT), "--dataset-root", str(self.root),
                               "--report-dir", str(Path(self.folder.name) / "report"), *extra],
                              capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})

    def test_shared_video_and_fixed_size_parquet_pass(self):
        before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in self.root.rglob("*") if path.is_file()}
        report = MODULE.inspect_dataset(self.root)
        self.assertEqual(report["summary"]["status"], "PASS", report["issues"])
        self.assertEqual(len(report["episodes"]), 2)
        self.assertTrue(all(row["video_checked"] for row in report["episodes"]))
        after = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_corrupt_parquet_is_reported(self):
        self.data.write_bytes(b"broken parquet")
        report = MODULE.inspect_dataset(self.root)
        self.assertEqual(report["summary"]["status"], "FAIL")
        self.assertIn("data_read", {issue["check"] for issue in report["issues"]})

    def test_corrupt_video_is_reported(self):
        self.video.write_bytes(b"broken video")
        report = MODULE.inspect_dataset(self.root)
        self.assertEqual(report["summary"]["status"], "FAIL")
        self.assertTrue(all(not row["video_checked"] for row in report["episodes"]))

    def test_nan_is_isolated_to_one_episode(self):
        self.rows[4]["action"][0] = float("nan")
        self.write_data()
        report = MODULE.inspect_dataset(self.root)
        self.assertEqual([row["status"] for row in report["episodes"]], ["PASS", "FAIL"])

    def test_cli_success_failure_and_overwrite_exit_codes(self):
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS", result.stdout)
        result = self.cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn("덮어쓰지", result.stderr)

    def test_cli_failure_writes_report(self):
        self.rows[4]["action"][0] = float("nan")
        self.write_data()
        result = self.cli()
        self.assertEqual(result.returncode, 1, result.stderr)
        summary = json.loads((Path(self.folder.name) / "report/summary.json").read_text())
        self.assertEqual(summary["status"], "FAIL")

    def test_cli_skip_video_is_not_success(self):
        result = self.cli("--skip-video")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("WARNING", result.stdout)


if __name__ == "__main__":
    unittest.main()
