import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RuntimeBoundaryTests(unittest.TestCase):
    def test_existing_runtime_is_never_overwritten(self):
        ssm = Mock()
        load("operate").prepare(ssm)
        ssm.get_parameter.assert_called_once_with(Name="/delta-platform/ai/runtime")
        ssm.put_parameter.assert_not_called()

    def test_team_image_is_rejected_before_secret_access(self):
        module = load("install")
        args = [
            "install.py",
            "--image",
            "111122223333.dkr.ecr.ap-northeast-2.amazonaws.com/delta-ai/api@sha256:" + "a" * 64,
            "--account-id",
            "111122223333",
            "--region",
            "ap-northeast-2",
        ]
        with (
            patch.object(sys, "argv", args),
            patch.object(module.subprocess, "check_output") as read,
        ):
            with self.assertRaises(ValueError):
                module.main()
        read.assert_not_called()

    def test_deploy_rejects_unpinned_image_before_s3_or_ssm(self):
        session = Mock()
        outputs = {"repository_url": "example.amazonaws.com/delta-platform-ai"}
        with self.assertRaises(ValueError):
            load("operate").deploy(session, outputs, outputs["repository_url"] + ":latest")
        session.client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
