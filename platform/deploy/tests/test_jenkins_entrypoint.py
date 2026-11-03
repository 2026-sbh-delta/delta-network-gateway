import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class JenkinsEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.common = load("build_common", ROOT / "builds/build.py")
        with patch.dict(sys.modules, {"build_common": self.common}):
            self.module = load("jenkins_build", ROOT / "jenkins/agent/build.py")
        self.env = {
            "DELTA_RUN_ID": "11111111-1111-4111-8111-111111111111",
            "DELTA_SOURCE": "https://github.com/docker/welcome-to-docker",
            "DELTA_COMMIT": "a" * 40,
            "DELTA_CONTEXT": ".",
            "DELTA_DOCKERFILE": "Dockerfile",
            "DELTA_ECR_REPOSITORY": "111122223333.dkr.ecr.ap-northeast-2.amazonaws.com/delta-platform-builds",
            "AWS_REGION": "ap-northeast-2",
        }

    def test_accepts_pinned_public_source(self):
        self.assertEqual(self.module.validated(self.env), self.env)

    def test_untrusted_fields_cannot_redirect_or_inject(self):
        for key, value in [
            ("DELTA_SOURCE", "https://github.com.attacker.test/a/b"),
            ("DELTA_SOURCE", "file:///etc"),
            ("DELTA_SOURCE", "https://user:pass@github.com/a/b"),
            ("DELTA_SOURCE", "https://github.com/a/b?cmd=whoami"),
            ("DELTA_COMMIT", "main"),
            ("DELTA_RUN_ID", "$(whoami)"),
            ("DELTA_CONTEXT", "../escape"),
            ("DELTA_DOCKERFILE", "/etc/passwd"),
            ("DELTA_DOCKERFILE", "Dockerfile\nexit"),
            ("AWS_REGION", "x;curl"),
            ("DELTA_ECR_REPOSITORY", "attacker.test/image"),
        ]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.module.validated({**self.env, key: value})

    def test_existing_path_guard_rejects_checkout_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Dockerfile").symlink_to("/etc/passwd")
            with self.assertRaises(ValueError):
                self.common.paths(root, ".", "Dockerfile")

    def test_runtime_merge_preserves_application_secrets_and_legacy_history_settings(
        self,
    ):
        with patch.dict(sys.modules, {"boto3": object()}):
            module = load("jenkins_runtime", ROOT / "jenkins/configure-runtime.py")
        current = {
            "db_password": "unchanged",
            "app_env": {
                "PLATFORM_LOGIN_ID": "unchanged",
                "PLATFORM_BUILD_PROJECT": "legacy",
                "PLATFORM_SANDBOX_TARGET": "unchanged",
            },
        }
        values = {
            "PLATFORM_BUILD_PROVIDER": "jenkins",
            "PLATFORM_BUILD_REPOSITORY": self.env["DELTA_ECR_REPOSITORY"],
            "PLATFORM_BUILD_REGION": "ap-northeast-2",
            "PLATFORM_JENKINS_URL": "https://ci.example.test",
            "PLATFORM_JENKINS_JOB": "delta-platform-image",
            "PLATFORM_JENKINS_USER": "platform",
            "PLATFORM_JENKINS_TOKEN": "test-only-token",
        }
        merged = module.merged_runtime(current, values, "111122223333")
        self.assertEqual(merged["db_password"], "unchanged")
        self.assertEqual(merged["app_env"]["PLATFORM_BUILD_PROJECT"], "legacy")
        self.assertEqual(merged["app_env"]["PLATFORM_SANDBOX_TARGET"], "unchanged")
        self.assertNotIn("PLATFORM_JENKINS_TOKEN", current["app_env"])
        for change in [
            {"PLATFORM_JENKINS_URL": "http://ci.example.test"},
            {"PLATFORM_LOGIN_ID": "overwrite"},
            {"PLATFORM_JENKINS_TOKEN": "token\nPLATFORM_LOGIN_ID=bad"},
            {"PLATFORM_BUILD_PROVIDER": "codebuild"},
        ]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                module.merged_runtime(current, {**values, **change}, "111122223333")
