import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "runtime_merge", Path(__file__).parents[1] / "scripts/read-runtime.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class OnpremRuntimeMergeTest(unittest.TestCase):
    def test_deployment_accepts_known_optional_keys_without_accepting_unknown_keys(
        self,
    ):
        base = {"app_env": {}}
        extra = {
            key: "configured"
            for key in module.DEPLOYMENT_KEYS | module.DEPLOYMENT_OPTIONAL_KEYS
        }
        merged = module.merge_component(
            base, extra, module.DEPLOYMENT_KEYS, module.DEPLOYMENT_OPTIONAL_KEYS
        )
        self.assertEqual(merged["app_env"], extra)
        with self.assertRaises(ValueError):
            module.merge_component(
                base,
                {**extra, "UNEXPECTED": "value"},
                module.DEPLOYMENT_KEYS,
                module.DEPLOYMENT_OPTIONAL_KEYS,
            )

    def test_deployment_sibling_is_bounded_and_preserves_other_components(self):
        base = {
            "app_env": {
                "PLATFORM_PASSWORD_HASH": "unchanged",
                "PLATFORM_ONPREM_SANDBOX_TOKEN": "keep",
            }
        }
        extra = {key: "configured" for key in module.DEPLOYMENT_KEYS}
        result = module.merge_component(base, extra, module.DEPLOYMENT_KEYS)
        self.assertEqual(result["app_env"]["PLATFORM_PASSWORD_HASH"], "unchanged")
        self.assertEqual(result["app_env"]["PLATFORM_ONPREM_SANDBOX_TOKEN"], "keep")
        with self.assertRaises(ValueError):
            module.merge_component(
                base,
                {**extra, "PLATFORM_PASSWORD_HASH": "replace"},
                module.DEPLOYMENT_KEYS,
            )

    def test_optional_component_preserves_existing_login_and_database(self):
        base = {
            "db_password": "unchanged",
            "app_env": {
                "PLATFORM_PUBLIC_ORIGIN": "https://delta.example.com",
                "PLATFORM_PASSWORD_HASH": "unchanged",
            },
        }
        self.assertIs(module.merge_onprem(base, None), base)
        extra = {
            "PLATFORM_ONPREM_SANDBOX_TARGET": "{}",
            "PLATFORM_ONPREM_SANDBOX_TOKEN": "scoped-token",
        }
        merged = module.merge_onprem(base, extra)
        self.assertEqual(merged["db_password"], base["db_password"])
        self.assertEqual(merged["app_env"]["PLATFORM_PASSWORD_HASH"], "unchanged")
        self.assertNotIn("PLATFORM_ONPREM_SANDBOX_TOKEN", base["app_env"])

    def test_other_settings_and_line_injection_are_rejected(self):
        for extra in (
            {"PLATFORM_PASSWORD_HASH": "replace"},
            {
                "PLATFORM_ONPREM_SANDBOX_TARGET": "{}",
                "PLATFORM_ONPREM_SANDBOX_TOKEN": "token\nOTHER=1",
            },
            {},
        ):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                module.merge_onprem({"app_env": {}}, extra)

    def test_conflicting_existing_component_is_rejected(self):
        with self.assertRaises(ValueError):
            module.merge_onprem(
                {"app_env": {"PLATFORM_ONPREM_SANDBOX_TOKEN": "old"}},
                {
                    "PLATFORM_ONPREM_SANDBOX_TARGET": "{}",
                    "PLATFORM_ONPREM_SANDBOX_TOKEN": "new",
                },
            )
