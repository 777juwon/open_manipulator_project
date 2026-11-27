"""원본 선택 범위, 검증 표시, 학습 명령을 장치와 학습 없이 확인한다."""

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prepare100", ROOT / "tools/prepare_omx_100ep.py")
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.base = Path(self.folder.name)
        self.features = {f"observation.images.{k}": {"shape": [480, 640, 3]} for k in ["front", "top"]}
        for _, name in prepare.SOURCES:
            p = self.base / name / "meta"
            p.mkdir(parents=True)
            (p / "info.json").write_text(json.dumps({"total_episodes": 30, "fps": 30,
                "codebase_version": "v3.0", "features": self.features}))

    def change_info(self, **changes):
        path = self.base / prepare.SOURCES[0][1] / "meta/info.json"
        data = json.loads(path.read_text())
        data.update(changes)
        path.write_text(json.dumps(data))

    def test_exactly_twenty_per_source_with_original_indices(self):
        plan = prepare.make_plan(self.base)
        self.assertEqual(sum(len(row["keep"]) for row in plan), 100)
        for item in plan:
            self.assertEqual(item["keep"], list(range(10, 30)))
            self.assertEqual(item["exclude"], list(range(10)))
        self.assertEqual(plan[-1]["task"], "Pick up the white tape and place it in the box.")

    def test_additional_episodes_outside_range_also_excluded(self):
        self.change_info(total_episodes=31)
        self.assertEqual(prepare.make_plan(self.base)[0]["exclude"], [*range(10), 30])

    def test_insufficient_episodes_rejected(self):
        self.change_info(total_episodes=29)
        with self.assertRaises(ValueError):
            prepare.make_plan(self.base)

    def test_incompatible_features_rejected(self):
        self.change_info(features={"observation.images.front": {}})
        with self.assertRaises(ValueError):
            prepare.make_plan(self.base)

    def test_signatures_ignore_episode_number_but_detect_action_changes(self):
        row = {"episode_index": 10, "frame_index": 0, "timestamp": 0.0,
               "observation.state": [1], "action": [2]}
        with patch.object(prepare, "read_rows", return_value=iter([row])):
            a = prepare.episode_signatures("original", [10])[10]
        other = dict(row, episode_index=0)
        with patch.object(prepare, "read_rows", return_value=iter([other])):
            b = prepare.episode_signatures("copy", [0])[0]
        self.assertEqual(a, b)
        other["action"] = [3]
        with patch.object(prepare, "read_rows", return_value=iter([other])):
            self.assertNotEqual(a, prepare.episode_signatures("copy", [0])[0])

    def test_duplicate_frame_indices_rejected(self):
        row = {"episode_index": 10, "frame_index": 0, "timestamp": 0.0,
               "observation.state": [1], "action": [2]}
        with patch.object(prepare, "read_rows", return_value=iter([row, row])):
            with self.assertRaises(ValueError):
                prepare.episode_signatures("original", [10])

    def test_prepared_marker_checks_metadata_hash(self):
        root = self.base / prepare.SOURCES[0][1]
        info = root / "meta/info.json"
        marker = {"status": "validated", "mapping": [{}] * 100,
                  "info_sha256": hashlib.sha256(info.read_bytes()).hexdigest()}
        (root / "selection_manifest.json").write_text(json.dumps(marker))
        self.assertEqual(prepare.verify_prepared(root), marker)
        info.write_text('{}')
        with self.assertRaises(ValueError):
            prepare.verify_prepared(root)

    def test_training_dry_run_has_two_cameras_lora_and_20k(self):
        result = subprocess.run(["bash", str(ROOT / "tools/train_omx_100ep_lora.sh"), "--dry-run"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for value in ["--peft.method_type=LORA", "--peft.r=16", "--peft.lora_alpha=16",
                      "--steps=20000", "--dataset.eval_split=0.1", "--batch_size=1",
                      "observation.images.front", "observation.images.top", "camera1", "camera2"]:
            self.assertIn(value, result.stdout)


if __name__ == "__main__":
    unittest.main()
