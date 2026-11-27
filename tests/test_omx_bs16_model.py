"""다운로드·장치 연결 없이 설정 호환성과 새 모델 실행 인자를 확인한다."""

import importlib.util
from pathlib import Path
import shlex
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("compat_bs16", ROOT / "tools/prepare_omx_bs16_model.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ModelCompatibilityTests(unittest.TestCase):
    def test_only_supported_empty_new_fields_removed(self):
        source = {"type": "smolvla", "use_amp": False, "dtype": None, "optimizer_fused": None}
        result = module.adapt_config(source, {"use_amp"})
        self.assertEqual(result, {"type": "smolvla", "use_amp": False})
        self.assertIn("dtype", source)

    def test_unknown_nonempty_setting_is_not_silently_dropped(self):
        for config in [{"dtype": "bfloat16"}, {"new_setting": None}]:
            with self.assertRaises(ValueError):
                module.adapt_config(config, set())

    def processor(self):
        return {"steps": [
            {"registry_name": "rename_observations_processor", "config": {"rename_map": module.MAPPING}},
            {"registry_name": "tokenizer_processor", "config": {"tokenizer_name": "tokenizer"},
             "artifacts": {"tokenizer_name": "tokenizer"}},
        ]}

    def test_tokenizer_resolves_locally_and_keeps_original(self):
        source = self.processor()
        result = module.adapt_processor(source, "/workspace/model/tokenizer")
        self.assertEqual(result["steps"][1]["config"]["tokenizer_name"], "/workspace/model/tokenizer")
        self.assertNotIn("artifacts", result["steps"][1])
        self.assertEqual(source["steps"][1]["config"]["tokenizer_name"], "tokenizer")

    def test_wrong_camera_order_rejected(self):
        source = self.processor()
        source["steps"][0]["config"]["rename_map"] = {
            "observation.images.front": "observation.images.camera1"}
        with self.assertRaises(ValueError):
            module.adapt_processor(source, "/tmp/tokenizer")

    def test_top_only_processor_preserves_single_camera(self):
        source = self.processor()
        mapping = {"observation.images.top": "observation.images.camera1"}
        source["steps"][0]["config"]["rename_map"] = mapping
        result = module.adapt_processor(source, "/tmp/tokenizer", mapping)
        self.assertEqual(result["steps"][0]["config"]["rename_map"], mapping)
        with self.assertRaises(ValueError):
            module.adapt_processor(source, "/tmp/tokenizer")

    def test_top_only_launcher_never_opens_wrist(self):
        result = subprocess.run(["bash", str(ROOT / "tools/run_omx_toponly.sh"),
                                 "red", "rtc", "15", "--dry-run"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        line = next(line for line in result.stdout.splitlines() if line.startswith("실행 예정 명령:"))
        args = shlex.split(line.split(":", 1)[1])
        cameras = next(a for a in args if a.startswith("--robot.cameras="))
        self.assertNotIn("front:", cameras)
        self.assertNotIn("HD_USB_Camera", cameras)
        self.assertIn("C270", cameras)
        self.assertEqual(cameras.count("fps: 30"), 1)
        self.assertIn('--rename_map={"observation.images.top":"observation.images.camera1"}', args)
        self.assertIn('--policy.path=/workspace/models/omx_smolvla_toponly_lora_20k_compat061', args)
        self.assertIn('--task=Pick up the red block and place it in the box.', args)
        self.assertIn(str(ROOT / 'tools/record_omx_rollout.py'), args)
        self.assertIn('--manual-evaluation', args)

    def test_launcher_uses_new_model_and_top_first(self):
        result = subprocess.run(["bash", str(ROOT / "tools/run_omx_bs16.sh"),
                                 "green", "rtc", "15", "--dry-run"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        line = next(line for line in result.stdout.splitlines() if line.startswith("실행 예정 명령:"))
        args = shlex.split(line.split(":", 1)[1])
        self.assertIn('--rename_map={"observation.images.top":"observation.images.camera1","observation.images.front":"observation.images.camera2"}', args)
        self.assertIn("--policy.path=/workspace/models/omx_smolvla_dualcam_lora_bs16_20k_compat061", args)
        for value in ["--fps=15", "--duration=0", "--return_to_initial_position=true",
                      "--inference.type=rtc", "--task=Pick up the green block and place it in the box."]:
            self.assertIn(value, args)
        self.assertIn("80ep_bs16_", result.stdout)


if __name__ == "__main__":
    unittest.main()
