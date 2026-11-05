"""Extract the fixed agent artifact without rebuilding or running the customer image."""

import argparse
import json
import subprocess
import uuid
from pathlib import Path

from profiles import NODE_IMAGE


def prepare(destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError("Use a fresh versioned agent directory")
    subprocess.run(
        ["docker", "pull", "--platform", "linux/amd64", NODE_IMAGE], check=True
    )
    name = "delta-otel-extract-" + uuid.uuid4().hex[:12]
    subprocess.run(
        ["docker", "create", "--platform", "linux/amd64", "--name", name, NODE_IMAGE],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    try:
        destination.mkdir(parents=True)
        subprocess.run(
            ["docker", "cp", name + ":/autoinstrumentation/.", str(destination)],
            check=True,
        )
        register = (
            destination
            / "node_modules/@opentelemetry/auto-instrumentations-node/build/src/register.js"
        )
        hook = destination / "node_modules/@opentelemetry/instrumentation/hook.mjs"
        if not register.is_file() or not hook.is_file():
            raise ValueError("Pinned agent does not contain the expected entry points")
        (destination / "delta-agent.json").write_text(
            json.dumps({"language": "nodejs", "image": NODE_IMAGE})
        )
    finally:
        subprocess.run(["docker", "rm", name], check=True, stdout=subprocess.DEVNULL)
    return destination


if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="고정 Node.js 계측 파일 준비; 앱 이미지 변경 없음"
    )
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    print(prepare(args.output))
