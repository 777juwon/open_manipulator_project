"""장치나 모델을 실행하지 않고 RTC 평가 명령의 설정을 검증한다."""

from pathlib import Path
import os
import shlex
import signal
import subprocess
import tempfile
import time
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "tools/run_omx_100ep.sh"


class RolloutLauncherTests(unittest.TestCase):
    def arguments(self, *args):
        result = subprocess.run(["bash", str(SCRIPT), *args, "--dry-run"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        line = next(line for line in result.stdout.splitlines() if line.startswith("실행 예정 명령:"))
        return shlex.split(line.split(":", 1)[1])

    def test_rtc_thirty_hz_preserves_training_camera_settings(self):
        args = self.arguments("green")
        self.assertIn("--inference.type=rtc", args)
        self.assertIn("--fps=30", args)
        self.assertIn("--inference.rtc.execution_horizon=10", args)
        cameras = next(arg for arg in args if arg.startswith("--robot.cameras="))
        self.assertEqual(cameras.count("fps: 30"), 2)
        self.assertEqual(cameras.count("fourcc: 'YUYV'"), 2)
        self.assertIn("--robot.id=omx_follower_arm", args)
        self.assertIn("--duration=0", args)
        self.assertIn("--return_to_initial_position=true", args)
        self.assertIn("--robot.max_relative_target=5", args)

    def test_sync_five_hz_does_not_change_camera_fps(self):
        args = self.arguments("red", "sync", "5")
        self.assertIn("--inference.type=sync", args)
        self.assertIn("--fps=5", args)
        self.assertFalse(any(arg.startswith("--inference.rtc.") for arg in args))
        self.assertIn("--task=Pick up the red block and place it in the box.", args)
        self.assertIn("fps: 30", next(arg for arg in args if arg.startswith("--robot.cameras=")))

    def test_final_checkpoint_and_tape_task(self):
        args = self.arguments("white_tape", "rtc", "30")
        model = next(arg for arg in args if arg.startswith("--policy.path="))
        self.assertIn("100ep", model)
        self.assertIn("/020000/pretrained_model", model)
        self.assertIn("--task=Pick up the white tape and place it in the box.", args)

    def test_invalid_inputs_rejected(self):
        for args in [[], ["purple"], ["green", "wrong"], ["green", "rtc", "0"],
                     ["green", "rtc", "30", "--bad"]]:
            with self.subTest(args=args):
                result = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)

    def test_recording_wrapper_retains_control_arguments(self):
        result = subprocess.run(["bash", str(SCRIPT), "red", "rtc", "15", "--dry-run"],
                                env={**os.environ, "OMX_RECORD_ROLLOUT": "1"}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        args = shlex.split(next(line for line in result.stdout.splitlines()
                               if line.startswith("실행 예정 명령:")).split(":", 1)[1])
        self.assertIn(str(SCRIPT.parent / "record_omx_rollout.py"), args)
        self.assertIn("--log-dir", args)
        self.assertIn("--", args)
        self.assertIn("--inference.type=rtc", args)
        self.assertIn("--robot.max_relative_target=5", args)

    @unittest.skipUnless(os.name == "posix", "프로세스 그룹 신호는 POSIX에서 확인합니다.")
    def test_ctrl_c_waits_for_fake_child_and_tee_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            model = root / "model"
            model.mkdir()
            for name in ["config.json", "adapter_config.json", "adapter_model.safetensors",
                         "policy_preprocessor.json", "policy_postprocessor.json"]:
                (model / name).touch()
            device = root / "not_a_device"
            device.touch()
            shim = root / "fake_python"
            shim.write_text('''#!/usr/bin/env python3
import os, signal, sys, time
from pathlib import Path
if "-c" in sys.argv:
    sys.exit(0)
stop = False
def handler(*args):
    global stop
    stop = True
signal.signal(signal.SIGINT, handler)
signal.signal(signal.SIGTERM, handler)
Path(os.environ["FAKE_READY"]).touch()
print("가상 실행 준비", flush=True)
while not stop:
    time.sleep(0.01)
time.sleep(0.1)
print("가상 파일 정리 완료", flush=True)
''')
            shim.chmod(0o755)
            env = {**os.environ, "OMX_PYTHON": str(shim), "OMX_100EP_MODEL_PATH": str(model),
                   "OMX_FOLLOWER_PORT": str(device), "OMX_TOP_CAMERA": str(device),
                   "OMX_ROLLOUT_CAMERA_LAYOUT": "top", "OMX_RECORD_ROLLOUT": "1",
                   "OMX_LOG_ROOT": str(root / "logs"), "FAKE_READY": str(root / "ready")}
            process = subprocess.Popen(["bash", str(SCRIPT), "red", "rtc", "15"], env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                       start_new_session=True)
            try:
                deadline = time.monotonic() + 5
                while not (root / "ready").exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue((root / "ready").exists())
                os.killpg(process.pid, signal.SIGINT)
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, stdout + stderr)
                log = next((root / "logs").glob("*"))
                self.assertIn("가상 파일 정리 완료", (log / "console.log").read_text())
                self.assertEqual((log / "exit_code.txt").read_text().strip(), "0")
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()


if __name__ == "__main__":
    unittest.main()
