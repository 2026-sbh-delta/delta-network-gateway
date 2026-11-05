"""Use a short-lived impersonated token for gcloud's OS Login path.

ADC still obtains credentials through AWS workload identity federation. Passing
the resulting service-account token avoids the CLI treating WIF as workforce SSH.
"""

import os
import tempfile
from pathlib import Path

import google.auth
from google.auth.transport.requests import Request


def ssh(args, account, runner):
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(Request())
    with tempfile.TemporaryDirectory(prefix="delta-gcp-ssh-") as root:
        token = Path(root) / "access-token"
        token.touch(mode=0o600)
        token.write_text(credentials.token)
        env = {
            k: v
            for k, v in os.environ.items()
            if k != "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"
        }
        return runner(
            [
                "gcloud",
                "compute",
                "ssh",
                *args,
                "--account=" + account,
                "--access-token-file=" + str(token),
            ],
            timeout=1000,
            env=env,
        )
