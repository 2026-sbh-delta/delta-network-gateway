"""Render immutable runtime images from an actual sandbox result, never a mutable tag."""

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parent / "manifests/runtime"


def render(result, *, preview=False):
    if result.get("runtime_check") != "passed" or result.get("cleanup") != "stopped":
        raise ValueError("Completed runtime and cleanup evidence required")
    if not preview and result.get("security_check") != "passed":
        raise ValueError(
            "Security evidence required; --preview only renders an unapproved candidate"
        )
    commit = result["commit_sha"]
    if not re.fullmatch("[a-f0-9]{40}", commit):
        raise ValueError("Full source commit required")
    components = result["components"]
    release = {
        "commit_sha": commit,
        "frontend": components["frontend"]["image"],
        "backend": components["backend"]["image"],
        "frontend_env": {
            "API_HOST": "sandbox-backend",
            "API_PORT": "8080",
            "API_PROTOCOL": "http",
        },
    }
    if (
        hashlib.sha256(json.dumps(release, sort_keys=True).encode()).hexdigest()
        != result["release_sha256"]
    ):
        raise ValueError("Release content changed after verification")
    for name in ("frontend", "backend"):
        if not re.fullmatch(
            r"[a-zA-Z0-9./_-]+@sha256:[a-f0-9]{64}", components[name]["image"]
        ):
            raise ValueError("Digest-pinned image required")
    objects = []
    for file in ("base.yaml", "network.yaml", "apps.yaml"):
        objects.extend(yaml.safe_load_all((ROOT / file).read_text()))
    for item in objects:
        item.setdefault("metadata", {}).setdefault("annotations", {})[
            "delta-platform/release-sha256"
        ] = result["release_sha256"]
        item["metadata"]["annotations"]["delta-platform/security-status"] = (
            "pending" if preview else "passed"
        )
        if item["kind"] == "Deployment":
            comp = item["metadata"]["name"]
            c = item["spec"]["template"]["spec"]["containers"][0]
            c["image"] = components[comp]["image"]
            for env in c["env"]:
                if env["name"] == "APP_VERSION":
                    env["value"] = commit
    return copy.deepcopy(objects)


def main():
    p = argparse.ArgumentParser(description="검증된 이미지의 운영 후보 YAML 생성")
    p.add_argument("--result", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--preview", action="store_true")
    a = p.parse_args()
    a.output.write_text(
        yaml.safe_dump_all(
            render(json.loads(a.result.read_text()), preview=a.preview), sort_keys=False
        )
    )


if __name__ == "__main__":
    main()
