"""데이터셋 구조 오류, 공유 영상 구간 및 원본 파일 보호를 검증한다."""

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("dataset_quality", ROOT / "tools/check_omx_dataset.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
NAMES = ["joint1", "joint2", "joint3", "joint4", "joint5", "gripper_joint_1"]
CAMERA = "observation.images.wrist"


class DatasetQualityTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name) / "dataset"
        self.paths = {
            "data": self.root / "data/chunk-000/file-000.parquet",
            "episodes": self.root / "meta/episodes/chunk-000/file-000.parquet",
            "tasks": self.root / "meta/tasks.parquet",
            "video": self.root / f"videos/{CAMERA}/chunk-000/file-000.mp4",
        }
        for path in self.paths.values():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        self.info = {
            "codebase_version": "v3.0", "fps": 30, "total_episodes": 1,
            "total_frames": 3, "total_tasks": 1,
            "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
            "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
            "features": {**{key: {"shape": [6], "names": NAMES} for key in ("observation.state", "action")},
                         CAMERA: {"dtype": "video", "shape": [480, 640, 3]}},
        }
        self.frames = [self.frame(i) for i in range(3)]
        self.metadata = [self.meta(0, 0, 3)]
        self.tasks = [{"task_index": 0, "task": "Pick up the cube"}]
        self.video = {"timestamps": [i / 30 for i in range(3)], "fps": 30, "size": (640, 480)}

    @staticmethod
    def frame(index, episode=0, start=0):
        return {"episode_index": episode, "frame_index": index, "timestamp": index / 30,
                "index": start + index, "task_index": 0, "observation.state": [0.1] * 6, "action": [0.2] * 6}

    @staticmethod
    def meta(episode, start, length):
        prefix = f"videos/{CAMERA}"
        return {"episode_index": episode, "length": length, "tasks": ["Pick up the cube"],
                "dataset_from_index": start, "dataset_to_index": start + length,
                "data/chunk_index": 0, "data/file_index": 0,
                f"{prefix}/chunk_index": 0, f"{prefix}/file_index": 0,
                f"{prefix}/from_timestamp": start / 30, f"{prefix}/to_timestamp": (start + length) / 30}

    def inspect(self, **kwargs):
        (self.root / "meta/info.json").write_text(json.dumps(self.info), encoding="utf-8")
        rows = {self.paths["data"]: self.frames, self.paths["episodes"]: self.metadata,
                self.paths["tasks"]: self.tasks}
        with patch.object(MODULE, "iter_parquet_rows", side_effect=lambda path: iter(rows[path])), \
                patch.object(MODULE, "probe_video", return_value=self.video) as probe:
            report = MODULE.inspect_dataset(self.root, **kwargs)
        return report, probe

    @staticmethod
    def checks(report):
        return {issue["check"] for issue in report["issues"]}

    def test_healthy_episode_passes(self):
        report, probe = self.inspect()
        self.assertEqual(report["summary"]["status"], "PASS")
        self.assertEqual(report["episodes"][0]["frame_count"], 3)
        self.assertTrue(report["episodes"][0]["video_checked"])
        self.assertEqual(probe.call_count, 1)

    def test_fifty_episodes_share_one_video(self):
        self.info.update(total_episodes=50, total_frames=150)
        self.frames = [self.frame(i, episode, 3 * episode) for episode in range(50) for i in range(3)]
        self.metadata = [self.meta(episode, 3 * episode, 3) for episode in range(50)]
        self.video["timestamps"] = [i / 30 for i in range(150)]
        report, probe = self.inspect()
        self.assertEqual(report["summary"]["status"], "PASS")
        self.assertEqual(len(report["episodes"]), 50)
        self.assertEqual(probe.call_count, 1)

    def test_shared_video_overlapping_episode_intervals_fail(self):
        self.info.update(total_episodes=2, total_frames=6)
        self.frames += [self.frame(i, 1, 3) for i in range(3)]
        second = self.meta(1, 3, 3)
        second[f"videos/{CAMERA}/from_timestamp"] = 0
        second[f"videos/{CAMERA}/to_timestamp"] = 0.1
        self.metadata.append(second)
        report, _ = self.inspect()
        self.assertEqual([row["status"] for row in report["episodes"]], ["FAIL", "FAIL"])
        self.assertIn(f"video_overlap:{CAMERA}", self.checks(report))

    def test_nan_and_wrong_joint_count_are_reported(self):
        self.frames[1]["observation.state"][2] = float("nan")
        self.frames[2]["action"] = [0.1]
        report, _ = self.inspect()
        self.assertEqual(report["summary"]["status"], "FAIL")
        self.assertEqual(report["episodes"][0]["invalid_state_rows"], 1)
        self.assertEqual(report["episodes"][0]["invalid_action_rows"], 1)

    def test_duplicate_and_missing_frames_are_reported(self):
        self.frames[2]["frame_index"] = 1
        report, _ = self.inspect()
        self.assertEqual(report["episodes"][0]["duplicate_frames"], 1)
        self.assertEqual(report["episodes"][0]["missing_frames"], 1)
        self.assertIn("frame_order", self.checks(report))

    def test_invalid_timestamp_is_reported(self):
        self.frames[1]["timestamp"] = float("inf")
        self.frames[2]["timestamp"] = -1
        report, _ = self.inspect()
        self.assertEqual(report["episodes"][0]["timestamp_errors"], 2)

    def test_metadata_counts_and_ranges_are_reported(self):
        self.info["total_frames"] = 30
        self.metadata[0]["dataset_to_index"] = 4
        report, _ = self.inspect()
        self.assertIn("total_frames", self.checks(report))
        self.assertIn("dataset_range", self.checks(report))
        self.assertEqual(report["episodes"][0]["status"], "FAIL")

    def test_unknown_task_is_reported(self):
        self.frames[1]["task_index"] = 4
        report, _ = self.inspect()
        self.assertEqual(report["episodes"][0]["task_errors"], 1)

    def test_feature_names_are_checked(self):
        self.info["features"]["action"]["names"] = ["joint1"] * 6
        report, _ = self.inspect()
        self.assertIn("feature_schema", self.checks(report))

    def test_missing_episode_metadata_is_reported(self):
        self.metadata = []
        report, _ = self.inspect()
        self.assertEqual(report["episodes"][0]["status"], "FAIL")
        self.assertIn("episode_metadata_missing", self.checks(report))

    def test_missing_data_episode_is_not_counted_as_observed(self):
        self.frames = []
        report, _ = self.inspect()
        self.assertEqual(report["summary"]["episode_count"], 0)
        self.assertEqual(report["episodes"][0]["missing_frames"], 3)
        self.assertIn("total_episodes", self.checks(report))

    def test_duplicate_metadata_is_reported(self):
        self.metadata.append(copy.deepcopy(self.metadata[0]))
        report, _ = self.inspect()
        self.assertIn("duplicate_episode_metadata", self.checks(report))

    def test_missing_video_is_reported(self):
        self.paths["video"].unlink()
        report, probe = self.inspect()
        self.assertEqual(report["summary"]["status"], "FAIL")
        self.assertFalse(report["episodes"][0]["video_checked"])
        probe.assert_not_called()

    def test_short_video_is_reported(self):
        self.video["timestamps"].pop()
        report, _ = self.inspect()
        self.assertEqual(report["summary"]["status"], "FAIL")
        self.assertIn(f"video:{CAMERA}", self.checks(report))

    def test_wrong_video_shape_and_fps_are_reported(self):
        for field, value in (("size", (10, 10)), ("fps", 60)):
            with self.subTest(field=field):
                original = self.video[field]
                self.video[field] = value
                report, _ = self.inspect()
                self.assertEqual(report["summary"]["status"], "FAIL")
                self.video[field] = original

    def test_decode_failure_is_reported(self):
        with patch.object(MODULE, "probe_video", side_effect=ValueError("손상된 영상")):
            # inspect 도우미가 probe를 덮어쓰므로 여기서는 직접 검사한다.
            (self.root / "meta/info.json").write_text(json.dumps(self.info), encoding="utf-8")
            rows = {self.paths["data"]: self.frames, self.paths["episodes"]: self.metadata,
                    self.paths["tasks"]: self.tasks}
            with patch.object(MODULE, "iter_parquet_rows", side_effect=lambda path: iter(rows[path])):
                report = MODULE.inspect_dataset(self.root)
        self.assertEqual(report["summary"]["status"], "FAIL")

    def test_skip_video_is_warning_not_pass(self):
        report, probe = self.inspect(skip_video=True)
        self.assertEqual(report["summary"]["status"], "WARNING")
        self.assertFalse(report["episodes"][0]["video_checked"])
        probe.assert_not_called()

    def test_metadata_path_cannot_escape_dataset(self):
        self.info["video_path"] = "../../outside.mp4"
        report, probe = self.inspect()
        self.assertEqual(report["summary"]["status"], "FAIL")
        probe.assert_not_called()

    def test_symlink_data_cannot_escape_dataset(self):
        outside = Path(self.folder.name) / "outside.parquet"
        outside.touch()
        self.paths["data"].unlink()
        self.paths["data"].symlink_to(outside)
        report, _ = self.inspect()
        self.assertEqual(report["summary"]["status"], "FAIL")
        self.assertIn("data_read", self.checks(report))

    def test_episode_spanning_two_parquet_files_passes(self):
        (self.root / "meta/info.json").write_text(json.dumps(self.info), encoding="utf-8")
        extra = self.root / "data/chunk-000/file-001.parquet"
        extra.touch()
        rows = {self.paths["data"]: self.frames[:1], extra: self.frames[1:],
                self.paths["episodes"]: self.metadata, self.paths["tasks"]: self.tasks}
        with patch.object(MODULE, "iter_parquet_rows", side_effect=lambda path: iter(rows[path])), \
                patch.object(MODULE, "probe_video", return_value=self.video):
            report = MODULE.inspect_dataset(self.root)
        self.assertEqual(report["summary"]["status"], "PASS")

    def test_report_files_and_original_protection(self):
        report, _ = self.inspect()
        original = (self.root / "meta/info.json").read_bytes()
        output = Path(self.folder.name) / "report"
        MODULE.write_report(report, output)
        self.assertEqual({p.name for p in output.iterdir()}, {"episodes.csv", "issues.csv", "summary.json"})
        self.assertEqual((self.root / "meta/info.json").read_bytes(), original)
        with self.assertRaises(FileExistsError):
            MODULE.write_report(report, output)
        with self.assertRaises(ValueError):
            MODULE.write_report(report, self.root / "meta/report")

    def test_failed_report_publication_leaves_no_partial_report(self):
        report, _ = self.inspect()
        output = Path(self.folder.name) / "report"
        original = Path.replace

        def fail_on_json(path, destination):
            if path.name == "summary.json":
                raise OSError("테스트 저장 실패")
            return original(path, destination)

        with patch.object(Path, "replace", fail_on_json):
            with self.assertRaises(OSError):
                MODULE.write_report(report, output)
        self.assertFalse(output.exists())
        self.assertFalse(list(Path(self.folder.name).glob(".omx_quality_*")))

    def test_global_index_and_frame_reordering_are_reported(self):
        self.frames[1], self.frames[2] = self.frames[2], self.frames[1]
        report, _ = self.inspect()
        self.assertIn("global_index", self.checks(report))
        self.assertIn("frame_order", self.checks(report))

    def test_invalid_fps_and_version_are_rejected(self):
        for key, value in (("fps", 0), ("codebase_version", "v2.1")):
            with self.subTest(key=key):
                original = self.info[key]
                self.info[key] = value
                with self.assertRaises(ValueError):
                    self.inspect()
                self.info[key] = original


if __name__ == "__main__":
    unittest.main()
