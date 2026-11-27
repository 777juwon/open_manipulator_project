"""장치를 열지 않고 수집 명령의 설정과 오류 전달을 검증한다."""

import json
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "tools/record_omx_dualcam.sh"
spec = importlib.util.spec_from_file_location("resume_check", SCRIPT.with_name("check_omx_resume.py"))
resume_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resume_check)


class LauncherTests(unittest.TestCase):
    def run_script(self, *args, env=None):
        return subprocess.run(["bash", str(SCRIPT), *args], env=env,
                              capture_output=True, text=True)

    def test_dry_run_creates_no_dataset_and_ignores_old_name(self):
        with tempfile.TemporaryDirectory() as folder:
            env = dict(os.environ, OMX_DATASET_BASE=folder, OMX_TIMING_BASE=folder,
                       OMX_RUN_NAME="old_green", OMX_TASK="old task")
            result = self.run_script("yellow", "10", "--dry-run", env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Pick up the yellow block", result.stdout)
            self.assertIn("omx_yellow_dualcam_", result.stdout)
            self.assertNotIn("old_green", result.stdout)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_invalid_options_do_not_launch(self):
        for args in [("red", "0"), ("red", "-1"), ("red", "1;exit"),
                     ("purple", "10"), ("red", "10", "wrong"),
                     ("red", "10", "public", "wrong")]:
            with self.subTest(args=args):
                self.assertEqual(self.run_script(*args).returncode, 2)

    def test_white_tape_task_and_dataset_name(self):
        result = self.run_script("white_tape", "10", "public", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("작업: Pick up the white tape and place it in the box.", result.stdout)
        self.assertIn("omx_white_tape_dualcam_", result.stdout)
        self.assertNotIn("white_tape block", result.stdout)
        self.assertIn("--dataset.private=false", result.stdout)

    def test_modes_and_argument_boundaries_and_exit_status(self):
        with tempfile.TemporaryDirectory(prefix="omx record ") as folder:
            base = Path(folder)
            stub = base / "fake-python"
            stub.write_text("#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\nsys.exit(7)\n")
            stub.chmod(0o755)
            device = base / "device with spaces"
            device.touch()
            env = dict(os.environ, OMX_PYTHON=str(stub), OMX_DATASET_BASE=str(base / "data"),
                       OMX_TIMING_BASE=str(base / "timing"), OMX_FOLLOWER_PORT=str(device),
                       OMX_LEADER_PORT=str(device), OMX_WRIST_CAMERA=str(device), OMX_TOP_CAMERA=str(device))
            for mode, push, private in [("public", "true", "false"),
                                        ("private", "true", "true"), ("local", "false", "true")]:
                with self.subTest(mode=mode):
                    result = self.run_script("blue", "10", mode, env=env)
                    self.assertEqual(result.returncode, 7, result.stderr)
                    args = json.loads(result.stdout.splitlines()[-1])
                    self.assertIn("--dataset.push_to_hub=" + push, args)
                    self.assertIn("--dataset.private=" + private, args)
                    self.assertIn("--dataset.single_task=Pick up the blue block and place it in the box.", args)
                    self.assertIn("--robot.port=" + str(device), args)
                    self.assertIn("--readable-videos", args)
                    self.assertIn("--resume=false", args)

    def test_missing_device_stops_before_python(self):
        env = dict(os.environ, OMX_PYTHON="/bin/true", OMX_FOLLOWER_PORT="/missing/omx/device")
        result = self.run_script("red", "10", "local", env=env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("장치 경로 없음", result.stderr)

    def test_resume_keeps_dataset_and_uses_new_timing_path(self):
        name = "omx_green_dualcam_original"
        results = [self.run_script("green", "10", "public", "--resume-dataset", name, "--dry-run")
                   for _ in range(2)]
        timing = []
        for result in results:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--dataset.root=/workspace/lerobot_datasets/" + name, result.stdout)
            self.assertIn("--dataset.repo_id=juwon777/" + name, result.stdout)
            self.assertIn("--resume=true", result.stdout)
            self.assertIn("--dataset.num_episodes=10", result.stdout)
            timing.append(next(line for line in result.stdout.splitlines() if line.startswith("시간 로그:")))
        self.assertNotEqual(*timing)

    def test_resume_rejects_paths_missing_value_and_duplicate_option(self):
        for suffix in [("--resume-dataset",), ("--resume-dataset", "../green"),
                       ("--resume-dataset", "--dry-run"),
                       ("--resume-dataset", "a", "--resume-dataset", "b")]:
            with self.subTest(suffix=suffix):
                self.assertEqual(self.run_script("green", "10", *suffix).returncode, 2)


class ResumeCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.task = "Pick up the green block and place it in the box."
        self.info = {
            "codebase_version": "v3.0", "robot_type": "omx_follower", "fps": 30,
            "total_episodes": 10,
            "features": {
                **{f"observation.images.{key}": {
                    "shape": [480, 640, 3], "dtype": "video", "info": {"video.fps": 30}}
                    for key in ["front", "top"]},
                **{key: {"shape": [6], "names": list(resume_check.JOINTS)}
                    for key in ["observation.state", "action"]},
            },
        }

    def test_existing_green_starts_at_ten(self):
        self.assertEqual(resume_check.validate_info(self.info, [{"task": self.task}], self.task), 10)

    def test_wrong_task_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "instruction"):
            resume_check.validate_info(self.info, [{"task": self.task}], self.task.replace("green", "red"))

    def test_camera_and_joint_order_mismatch_rejected(self):
        self.info["features"]["action"]["names"].reverse()
        with self.assertRaises(ValueError):
            resume_check.validate_info(self.info, [{"task": self.task}], self.task)
        self.info["features"]["action"]["names"].reverse()
        self.info["features"].pop("observation.images.top")
        with self.assertRaises(ValueError):
            resume_check.validate_info(self.info, [{"task": self.task}], self.task)


if __name__ == "__main__":
    unittest.main()
