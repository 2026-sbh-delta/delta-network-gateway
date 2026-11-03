import json
import runpy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


class SandboxRuntimeTests(unittest.TestCase):
    def test_only_sandbox_config_changes_and_foreign_targets_are_rejected(self):
        path = Path(__file__).resolve().parents[1] / "sandbox/configure-runtime.py"
        with patch.dict(sys.modules, {"boto3": object()}):
            merge = runpy.run_path(str(path))["merged_runtime"]
        account = "111122223333"
        target = {
            "account_id": account,
            "region": "ap-northeast-2",
            "cluster": "delta-platform-sandbox",
            "service": "delta-platform-sandbox",
            "task_family": "delta-platform-sandbox",
            "allowed_image_prefixes": [
                f"{account}.dkr.ecr.ap-northeast-2.amazonaws.com/delta-platform-builds@"
            ],
            "dynamic_scanner_access": False,
            "target_group_arn": f"arn:aws:elasticloadbalancing:ap-northeast-2:{account}:targetgroup/delta-platform-sandbox/123",
        }
        current = {
            "db_password": "fixture",
            "app_env": {
                "PLATFORM_PASSWORD_HASH": "unchanged",
                "PLATFORM_BUILD_PROJECT": "unchanged",
            },
        }
        values = {"PLATFORM_SANDBOX_TARGET": json.dumps(target)}
        merged = merge(current, values, account)
        self.assertEqual(merged["db_password"], "fixture")
        for key, value in current["app_env"].items():
            self.assertEqual(merged["app_env"][key], value)
        self.assertNotIn("PLATFORM_SANDBOX_TARGET", current["app_env"])
        for change in (
            {"account_id": "999999999999"},
            {"service": "delta-lab-staging"},
            {"dynamic_scanner_access": True},
            {"allowed_image_prefixes": ["public.ecr.aws/"]},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                merge(
                    current,
                    {"PLATFORM_SANDBOX_TARGET": json.dumps({**target, **change})},
                    account,
                )
        with self.assertRaises(ValueError):
            merge(current, {**values, "PLATFORM_LOGIN_ID": "overwrite"}, account)
        with self.assertRaises(ValueError):
            merge(
                current,
                {"PLATFORM_SANDBOX_TARGET": json.dumps(target, indent=2)},
                account,
            )
