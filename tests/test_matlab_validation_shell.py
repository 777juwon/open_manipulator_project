import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "tools" / "omx_matlab_validation.sh"
HELPER = ROOT / "tools" / "omx_matlab_env.sh"


class MatlabCommunicationTests(unittest.TestCase):
    def run_bash(self, command, *args):
        env = dict(os.environ, ROS_DOMAIN_ID="30", RMW_IMPLEMENTATION="rmw_zenoh_cpp")
        return subprocess.run(
            ["bash", "-c", command, "test", *map(str, args)],
            env=env, capture_output=True, text=True, check=False,
        )

    def test_source_overrides_container_defaults(self):
        result = self.run_bash(
            'source "$1" && printf "%s\\n" "$ROS_DOMAIN_ID" '
            '"$RMW_IMPLEMENTATION" "$FASTRTPS_DEFAULT_PROFILES_FILE"', HELPER,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            "0", "rmw_fastrtps_cpp", str(ROOT / "tools" / "fastdds_udp_only.xml"),
        ])

    def test_source_resolves_a_project_path_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix="omx validation ") as folder:
            tools_dir = Path(folder) / "tools"
            tools_dir.mkdir()
            helper = tools_dir / HELPER.name
            shutil.copyfile(HELPER, helper)
            profile = tools_dir / "fastdds_udp_only.xml"
            profile.write_text("<profiles/>\n")
            result = self.run_bash(
                'source "$1" && printf "%s" "$FASTRTPS_DEFAULT_PROFILES_FILE"', helper,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, str(profile))

    def test_missing_profile_fails_before_launching_ros(self):
        with tempfile.TemporaryDirectory() as folder:
            tools_dir = Path(folder) / "tools"
            tools_dir.mkdir()
            helper = tools_dir / HELPER.name
            shutil.copyfile(HELPER, helper)
            result = self.run_bash('source "$1"', helper)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("OMX:MissingDDSProfile", result.stderr)

    def test_env_mode_requires_no_ros_installation(self):
        result = self.run_bash('bash "$1" env', LAUNCHER)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ROS_DOMAIN_ID=0\n", result.stdout)
        self.assertIn("RMW_IMPLEMENTATION=rmw_fastrtps_cpp\n", result.stdout)

    def test_help_does_not_start_collection(self):
        result = self.run_bash('bash "$1" --help', LAUNCHER)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("성공 에피소드 1개", result.stdout)
        self.assertNotIn("데이터셋 저장 위치:", result.stdout)

    def test_invalid_mode_and_extra_arguments_are_rejected(self):
        for args in (("unexpected",), ("collect", "--episodes", "50")):
            with self.subTest(args=args):
                result = self.run_bash('bash "$@"', LAUNCHER, *args)
                self.assertEqual(result.returncode, 2, result.stderr)

    def test_manifest_prevents_the_setuptools_conflict(self):
        requirements = (ROOT / "open_manipulator_lerobot" / "requirements.txt").read_text()
        self.assertIn("setuptools>=71,<80", requirements.splitlines())


if __name__ == "__main__":
    unittest.main()
