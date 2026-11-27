"""원격 변경 없이 삭제 범위와 오래된 조각 파일 정리 범위를 검증한다."""

import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location(
    "removegreen", Path(__file__).resolve().parents[1] / "tools/remove_green_10_29.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RemoveGreenTests(unittest.TestCase):
    def test_exactly_indices_ten_through_twenty_nine(self):
        self.assertEqual(module.REMOVE, list(range(10, 30)))
        self.assertEqual([i for i in range(30) if i not in module.REMOVE], list(range(10)))

    def test_unexpected_episode_count_rejected(self):
        for count in [10, 29, 31, None]:
            with self.subTest(count=count), self.assertRaises(ValueError):
                module.validate_original({"total_episodes": count, "fps": 30, "robot_type": "omx_follower"})
        module.validate_original({"total_episodes": 30, "fps": 30, "robot_type": "omx_follower"})

    def test_only_obsolete_dataset_fragments_deleted(self):
        remote = ["data/chunk-000/file-000.parquet", "data/chunk-000/file-001.parquet",
                  "videos/front/old.mp4", "meta/old.parquet", "README.md", ".gitattributes", "notes.txt"]
        local = {"data/chunk-000/file-000.parquet", "meta/info.json"}
        self.assertEqual(module.obsolete_files(remote, local),
                         ["data/chunk-000/file-001.parquet", "meta/old.parquet", "videos/front/old.mp4"])


if __name__ == "__main__":
    unittest.main()
