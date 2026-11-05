"""Prepare the existing tooling host; never print the platform token."""

import argparse
import json
import os
import re
import secrets
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

BASE = Path("/opt/delta")


def target(url):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or not re.fullmatch(
            r"internal-delta-platform-[a-z0-9-]+\.ap-northeast-2\.elb\.amazonaws\.com",
            parsed.netloc,
        )
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Expected the existing private platform ALB origin in Seoul")
    return {
        "id": "platform-sandbox",
        "label": "플랫폼 AWS ECS 샌드박스",
        "origin": url,
        "scenarios": ["vulnerable"],
        "entry_path": "/",
        "allowed_path_prefixes": ["/"],
        "routes": [],
        "repo": None,
    }


def prepare(url, root=BASE, gcp_env=None):
    definition = target(url)
    with urllib.request.urlopen(
        "http://127.0.0.1:8765/api/runs", timeout=10
    ) as response:
        records = json.load(response).get("runs")
    if not isinstance(records, list) or any(
        r.get("status") == "running" for r in records
    ):
        raise ValueError("Wait for active DAST scans before configuring the target")
    root.mkdir(parents=True, exist_ok=True)
    env = root / "dast-platform.env"
    values = {}
    if env.exists():
        values = dict(
            line.split("=", 1) for line in env.read_text().splitlines() if line
        )
        token = values.get("DAST_PLATFORM_TOKEN", "")
        if not re.fullmatch(r"[a-f0-9]{64}", token):
            raise ValueError("Existing DAST platform token format is invalid")
    else:
        token = secrets.token_hex(32)
    values.update(
        {"DAST_PLATFORM_TOKEN": token, "DAST_PLATFORM_TARGET_ID": "platform-sandbox"}
    )
    if gcp_env is not None:
        if set(gcp_env) != {"DAST_GCP_TARGET", "DAST_GCP_CREDENTIALS_JSON"}:
            raise ValueError("Expected only GCP DAST settings")
        if any(c in value for value in gcp_env.values() for c in "\r\n"):
            raise ValueError("GCP settings must be single-line JSON")
        if (
            json.loads(gcp_env["DAST_GCP_CREDENTIALS_JSON"]).get("type")
            != "external_account"
        ):
            raise ValueError("Keyless federation configuration is required")
        values.update(gcp_env)
    for path, value, mode in (
        (
            env,
            "".join(f"{key}={value}\n" for key, value in values.items()),
            0o600,
        ),
        (
            root / "platform-sandbox.json",
            json.dumps(definition, ensure_ascii=False),
            0o644,
        ),
    ):
        temporary = path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(fd, "w") as out:
            out.write(value)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sandbox-url", required=True)
    parser.add_argument("--gcp-outputs", type=Path)
    args = parser.parse_args()
    gcp_env = (
        json.loads(args.gcp_outputs.read_text())["dast_env"]["value"]
        if args.gcp_outputs
        else None
    )
    prepare(args.sandbox_url, gcp_env=gcp_env)
    print(
        "DAST target and token prepared. Redeploy the merged tooling image to load them."
    )
