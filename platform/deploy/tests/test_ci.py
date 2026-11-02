"""배포 경계: 잘못된 계정·초기 수신 규칙·다른 코드의 Plan 적용을 거부한다."""

import io
import json
import os
import runpy
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


class CIConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "scripts").mkdir()
        (self.root / "terraform").mkdir()
        shutil.copy(SCRIPTS / "ci-config.py", self.root / "scripts")
        self.config = {
            "account_id": "123456789012",
            "region": "ap-northeast-2",
            "availability_zone": "ap-northeast-2a",
            "ami_id": "ami-test",
            "bootstrap_origin_access": False,
        }

    def configure(self, **overrides):
        env = {
            "PLATFORM_TF_INPUTS": json.dumps(self.config),
            "PLATFORM_STATE_BUCKET": "test-platform-state",
            "AWS_REGION": "ap-northeast-2",
            "AWS_ACCOUNT_ID": "123456789012",
        } | overrides
        with patch.dict(os.environ, env):
            runpy.run_path(str(self.root / "scripts/ci-config.py"))

    def test_same_state_and_locking(self):
        self.configure()
        content = (self.root / "terraform/backend.hcl").read_text()
        self.assertIn('key = "platform/terraform.tfstate"', content)
        self.assertIn("use_lockfile = true", content)

    def test_wrong_account_rejected(self):
        with self.assertRaises(SystemExit):
            self.configure(AWS_ACCOUNT_ID="999999999999")

    def test_bootstrap_rule_rejected(self):
        self.config["bootstrap_origin_access"] = True
        with self.assertRaises(SystemExit):
            self.configure()

    def test_backend_injection_rejected(self):
        with self.assertRaises(SystemExit):
            self.configure(PLATFORM_STATE_BUCKET='bucket"\nkey="another-state')


class PlanProvenanceTests(unittest.TestCase):
    def verify(self, **overrides):
        run = {
            "conclusion": "success",
            "event": "workflow_dispatch",
            "path": ".github/workflows/platform-infra.yml",
            "head_branch": "main",
            "head_sha": "a" * 40,
        } | overrides
        with (
            patch.dict(
                os.environ,
                {
                    "PLAN_RUN_ID": "1234",
                    "GITHUB_SHA": "a" * 40,
                    "GITHUB_REPOSITORY": "example/platform",
                },
            ),
            patch("subprocess.check_output", return_value=json.dumps(run)),
        ):
            runpy.run_path(str(SCRIPTS / "verify-plan-run.py"))

    def test_matching_plan(self):
        self.verify()

    def test_wrong_source_or_commit_rejected(self):
        for change in (
            {"head_sha": "b" * 40},
            {"head_branch": "feature"},
            {"event": "pull_request"},
            {"path": ".github/workflows/unrelated.yml"},
            {"conclusion": "failure"},
        ):
            with self.subTest(change=change), self.assertRaises(SystemExit):
                self.verify(**change)


class ExistingRuntimeTests(unittest.TestCase):
    def deploy(self, initialized):
        sdk = MagicMock()
        hashing = MagicMock()
        sts, ssm, s3 = MagicMock(), MagicMock(), MagicMock()
        sdk.Session.return_value.client.side_effect = {
            "sts": sts,
            "ssm": ssm,
            "s3": s3,
        }.get
        sts.get_caller_identity.return_value = {"Account": "123456789012"}
        ssm.describe_parameters.return_value = {
            "Parameters": [{"Name": "/delta-platform/runtime", "Type": "SecureString"}]
            if initialized
            else []
        }
        ssm.send_command.return_value = {"Command": {"CommandId": "test-command"}}
        ssm.get_command_invocation.return_value = {
            "Status": "Success",
            "ResponseCode": 0,
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outputs = root / "outputs.json"
            images = root / "images.json"
            outputs.write_text(
                json.dumps(
                    {
                        key: {"value": value}
                        for key, value in {
                            "account_id": "123456789012",
                            "region": "ap-northeast-2",
                            "runtime_parameter": "/delta-platform/runtime",
                            "platform_url": "https://example.test",
                            "artifact_bucket": "test-artifacts",
                            "instance_id": "i-test",
                        }.items()
                    }
                )
            )
            images.write_text(
                json.dumps(
                    {"platform": "example@sha256:abc", "postgres": "example@sha256:def"}
                )
            )
            argv = [
                "deploy-release.py",
                "--outputs",
                str(outputs),
                "--images",
                str(images),
                "--runtime-dir",
                str(root / "runtime"),
                "--existing-runtime",
            ]
            # The CI path must never request plaintext secrets or generate credentials.
            with (
                patch.dict(
                    sys.modules,
                    {
                        "boto3": sdk,
                        "argon2": hashing,
                        "botocore.exceptions": MagicMock(),
                    },
                ),
                patch.object(sys, "argv", argv),
                redirect_stdout(io.StringIO()),
            ):
                if initialized:
                    runpy.run_path(str(SCRIPTS / "deploy-release.py"))
                else:
                    with self.assertRaises(SystemExit):
                        runpy.run_path(str(SCRIPTS / "deploy-release.py"))
            ssm.get_parameter.assert_not_called()
            ssm.put_parameter.assert_not_called()
            hashing.PasswordHasher.assert_not_called()
            self.assertFalse((root / "runtime/platform-login.txt").exists())
            if initialized:
                ssm.send_command.assert_called_once()
            else:
                ssm.send_command.assert_not_called()
                s3.put_object.assert_not_called()

    def test_existing_runtime_keeps_secrets_off_ci(self):
        self.deploy(True)

    def test_missing_runtime_stops_before_upload_or_deploy(self):
        self.deploy(False)


if __name__ == "__main__":
    unittest.main()
