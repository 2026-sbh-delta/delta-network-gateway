import importlib.util
import io
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "runtime_merge", Path(__file__).parents[1] / "scripts/read-runtime.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class OnpremRuntimeMergeTest(unittest.TestCase):
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

    def test_optional_pipeline_settings_preserve_required_deployment_values(self):
        base = {"app_env": {"PLATFORM_PASSWORD_HASH": "unchanged"}}
        required = {key: "configured" for key in module.DEPLOYMENT_KEYS}
        optional = {
            key: "pipeline-configured" for key in module.DEPLOYMENT_OPTIONAL_KEYS
        }
        for extra in (required, {**required, **optional}):
            with self.subTest(optional=bool(set(extra) - module.DEPLOYMENT_KEYS)):
                result = module.merge_component(
                    base, extra, module.DEPLOYMENT_KEYS, module.DEPLOYMENT_OPTIONAL_KEYS
                )
                self.assertEqual(result["app_env"], {**base["app_env"], **extra})
                self.assertEqual(
                    base["app_env"], {"PLATFORM_PASSWORD_HASH": "unchanged"}
                )

    def test_optional_settings_do_not_relax_required_or_unknown_key_checks(self):
        complete = {
            key: "configured"
            for key in module.DEPLOYMENT_KEYS | module.DEPLOYMENT_OPTIONAL_KEYS
        }
        invalid = [
            {key: value for key, value in complete.items() if key != missing}
            for missing in module.DEPLOYMENT_KEYS
        ]
        invalid.append({**complete, "PLATFORM_PASSWORD_HASH": "replace"})
        for extra in invalid:
            with self.subTest(keys=sorted(extra)), self.assertRaises(ValueError):
                module.merge_component(
                    {"app_env": {}},
                    extra,
                    module.DEPLOYMENT_KEYS,
                    module.DEPLOYMENT_OPTIONAL_KEYS,
                )

    def test_optional_pipeline_values_are_validated_and_conflicts_are_rejected(self):
        required = {key: "configured" for key in module.DEPLOYMENT_KEYS}
        for key in module.DEPLOYMENT_OPTIONAL_KEYS:
            for value in ("", "value\nOTHER=1", "value\rOTHER=1", None, {}, 1):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    module.merge_component(
                        {"app_env": {}},
                        {**required, key: value},
                        module.DEPLOYMENT_KEYS,
                        module.DEPLOYMENT_OPTIONAL_KEYS,
                    )
            with self.subTest(conflicting=key), self.assertRaises(ValueError):
                module.merge_component(
                    {"app_env": {key: "existing"}},
                    {**required, key: "different"},
                    module.DEPLOYMENT_KEYS,
                    module.DEPLOYMENT_OPTIONAL_KEYS,
                )
            result = module.merge_component(
                {"app_env": {key: "existing"}},
                {**required, key: "existing"},
                module.DEPLOYMENT_KEYS,
                module.DEPLOYMENT_OPTIONAL_KEYS,
            )
            self.assertEqual(result["app_env"][key], "existing")

    def test_main_applies_optional_pipeline_schema_to_deployment_sibling(self):
        extra = {
            key: "configured"
            for key in module.DEPLOYMENT_KEYS | module.DEPLOYMENT_OPTIONAL_KEYS
        }
        response = {
            "Parameters": [
                {
                    "Name": "/delta-platform/deployment/runtime",
                    "Type": "SecureString",
                    "Value": json.dumps(extra),
                }
            ],
            "InvalidParameters": ["/delta-platform/onprem/runtime"],
        }
        output = io.StringIO()
        with (
            patch.object(
                module.sys,
                "argv",
                [
                    "read-runtime.py",
                    "--parameter",
                    "/delta-platform/runtime",
                    "--region",
                    "r",
                ],
            ),
            patch.object(module.sys, "stdin", io.StringIO('{"app_env": {}}')),
            patch.object(module.sys, "stdout", output),
            patch.object(
                module.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0, stdout=json.dumps(response)),
            ),
        ):
            module.main()
        self.assertEqual(json.loads(output.getvalue())["app_env"], extra)

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
