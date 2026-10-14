"""관절 배열 펼치기와 보기용 파일 보호를 로봇 연결 없이 검증한다."""

import csv
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("dataset_csv", ROOT / "tools/export_omx_dataset_csv.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
NAMES = ["joint1", "joint2", "joint3", "joint4", "joint5", "gripper_joint_1"]


def frame():
    return {
        "episode_index": 0, "frame_index": 1, "timestamp": 1 / 30,
        "index": 1, "task_index": 0,
        "observation.state": [0.1] * 6, "action": [0.2] * 6,
    }


def fake_arrow(rows):
    parquet = types.ModuleType("pyarrow.parquet")
    parquet.ParquetFile = lambda _: types.SimpleNamespace(
        iter_batches=lambda **_: [types.SimpleNamespace(to_pylist=lambda: rows)])
    arrow = types.ModuleType("pyarrow")
    arrow.parquet = parquet
    return {"pyarrow": arrow, "pyarrow.parquet": parquet}


class DatasetCSVTests(unittest.TestCase):
    def make_dataset(self, folder):
        root = Path(folder) / "dataset"
        (root / "meta").mkdir(parents=True)
        (root / "data/chunk-000").mkdir(parents=True)
        info = {"features": {key: {"names": NAMES} for key in ("observation.state", "action")}}
        (root / "meta/info.json").write_text(json.dumps(info), encoding="utf-8")
        (root / "data/chunk-000/file-000.parquet").touch()
        return root

    def test_flatten_six_state_and_action_values(self):
        row = MODULE.flatten_frame(frame(), NAMES, NAMES)
        self.assertEqual(len(row), 17)
        self.assertEqual(row["state_joint1"], 0.1)
        self.assertEqual(row["action_gripper_joint_1"], 0.2)
        self.assertEqual(row["timestamp"], 1 / 30)

    def test_mismatched_vector_is_rejected(self):
        row = frame()
        row["action"] = [0.2]
        with self.assertRaises(ValueError):
            MODULE.flatten_frame(row, NAMES, NAMES)

    def test_duplicate_names_are_rejected(self):
        info = {"features": {"action": {"names": ["joint1", "joint1"]}}}
        with self.assertRaises(ValueError):
            MODULE.feature_names(info, "action")

    def test_export_writes_flat_values(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.make_dataset(folder)
            output = Path(folder) / "preview.csv"
            with patch.dict(sys.modules, fake_arrow([frame()])):
                count = MODULE.export_dataset_csv(root, output)
            self.assertEqual(count, 1)
            with output.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(float(rows[0]["state_joint1"]), 0.1)
            self.assertEqual(float(rows[0]["action_joint1"]), 0.2)
            self.assertFalse(list(Path(folder).glob("*.tmp")))

    def test_failed_export_leaves_no_partial_csv(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.make_dataset(folder)
            output = Path(folder) / "preview.csv"
            bad = frame()
            bad["action"] = [0.2]
            with patch.dict(sys.modules, fake_arrow([frame(), bad])):
                with self.assertRaises(ValueError):
                    MODULE.export_dataset_csv(root, output)
            self.assertFalse(output.exists())
            self.assertFalse(list(Path(folder).glob("*.tmp")))

    def test_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.make_dataset(folder)
            output = Path(folder) / "preview.csv"
            output.write_text("original\n", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                MODULE.export_dataset_csv(root, output)
            self.assertEqual(output.read_text(), "original\n")

    def test_non_csv_output_is_rejected(self):
        with self.assertRaises(ValueError):
            MODULE.export_dataset_csv("unused", "info.json")


if __name__ == "__main__":
    unittest.main()
