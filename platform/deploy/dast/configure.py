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


def prepare(url, root=BASE):
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
    if env.exists():
        values = dict(
            line.split("=", 1) for line in env.read_text().splitlines() if line
        )
        token = values.get("DAST_PLATFORM_TOKEN", "")
        if not re.fullmatch(r"[a-f0-9]{64}", token):
            raise ValueError("Existing DAST platform token format is invalid")
    else:
        token = secrets.token_hex(32)
    for path, value, mode in (
        (
            env,
            "DAST_PLATFORM_TOKEN="
            + token
            + "\nDAST_PLATFORM_TARGET_ID=platform-sandbox\n",
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
    args = parser.parse_args()
    prepare(args.sandbox_url)
    print(
        "DAST target and token prepared. Redeploy the merged tooling image to load them."
    )
