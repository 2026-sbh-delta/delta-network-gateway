import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "builds" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildEntrypointTests(unittest.TestCase):
    def test_context_cannot_escape_or_use_symlink_alias(self):
        module = load("trusted_build", "build.py")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app").mkdir()
            (root / "app" / "Dockerfile").write_text("FROM scratch\n")
            ctx, df = module.paths(root, "app", "app/Dockerfile")
            self.assertEqual(df.parent, ctx)
            (root / "alias").symlink_to(root / "app", target_is_directory=True)
            for context, dockerfile in [
                ("..", "app/Dockerfile"),
                ("alias", "alias/Dockerfile"),
                ("/tmp", "app/Dockerfile"),
            ]:
                with self.assertRaises(ValueError):
                    module.paths(root, context, dockerfile)

    def test_runtime_merge_preserves_login_and_database(self):
        # Import only if deploy SDK is installed; CI runs deployment tests with plain Python.
        import sys
        from unittest.mock import patch

        with patch.dict(sys.modules, {"boto3": object()}):
            module = load("build_runtime", "configure-runtime.py")
        current = {
            "db_password": "fixture",
            "app_env": {
                "PLATFORM_LOGIN_ID": "existing",
                "PLATFORM_PASSWORD_HASH": "existing-hash",
            },
        }
        values = {
            "PLATFORM_BUILD_PROJECT": "delta-platform-builds",
            "PLATFORM_BUILD_REGION": "ap-northeast-2",
            "PLATFORM_BUILD_REPOSITORY": "111122223333.dkr.ecr.ap-northeast-2.amazonaws.com/delta-platform-builds",
        }
        merged = module.merged_runtime(current, values, "111122223333")
        self.assertEqual(merged["db_password"], current["db_password"])
        self.assertEqual(merged["app_env"]["PLATFORM_PASSWORD_HASH"], "existing-hash")
        self.assertNotIn("PLATFORM_BUILD_PROJECT", current["app_env"])
        with self.assertRaises(ValueError):
            module.merged_runtime(current, values, "999999999999")
        with self.assertRaises(ValueError):
            module.merged_runtime(
                current, {**values, "PLATFORM_LOGIN_ID": "overwrite"}, "111122223333"
            )
