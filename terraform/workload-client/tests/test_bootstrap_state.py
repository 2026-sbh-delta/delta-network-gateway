"""Ensure state bootstrap cannot mutate AWS before identity/ownership checks."""
import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap_state.py"
SPEC = importlib.util.spec_from_file_location("bootstrap_state", SOURCE)
BOOTSTRAP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOOTSTRAP)


class BootstrapGuards(unittest.TestCase):
    def invoke(self, account, bucket_error=None, execute=False):
        argv = ["bootstrap_state.py", "--profile", "mock", "--account-id", "444455556666", "--bucket", "mock-state-bucket"]
        if execute:
            argv.append("--execute")
        responses = [subprocess.CompletedProcess([], 0, '{"Account":"' + account + '"}', "")]
        if bucket_error:
            responses.append(subprocess.CompletedProcess([], 254, "", bucket_error))
        with patch.object(sys, "argv", argv), patch.object(BOOTSTRAP.subprocess, "run", side_effect=responses) as runner, contextlib.redirect_stdout(io.StringIO()):
            try:
                BOOTSTRAP.main()
                failed = False
            except SystemExit:
                failed = True
            commands = [call.args[0][1:3] for call in runner.call_args_list]
        self.assertTrue(all(command in [["sts", "get-caller-identity"], ["s3api", "head-bucket"]] for command in commands))
        return failed, commands

    def test_wrong_account_cannot_execute(self):
        failed, commands = self.invoke("777788889999", execute=True)
        self.assertTrue(failed)
        self.assertEqual(commands, [["sts", "get-caller-identity"]])

    def test_unverified_bucket_owner_cannot_execute(self):
        failed, _ = self.invoke("444455556666", "An error occurred (403)", execute=True)
        self.assertTrue(failed)

    def test_preview_missing_bucket_does_not_create(self):
        failed, _ = self.invoke("444455556666", "An error occurred (404)")
        self.assertFalse(failed)


if __name__ == "__main__":
    unittest.main()
