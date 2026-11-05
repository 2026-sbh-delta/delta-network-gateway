"""Review/apply one bounded sandbox policy; approval is bound to content and live revision."""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

import yaml

NAME = "backend-from-frontend"
NS = "delta-platform-sandbox"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def expected():
    values = yaml.safe_load_all(
        (Path(__file__).parent / "manifests/sandbox/network.yaml").read_text()
    )
    return next(
        v
        for v in values
        if v["kind"] == "NetworkPolicy" and v["metadata"]["name"] == NAME
    )


def validate(candidate):
    ref = expected()
    if candidate != ref:
        raise ValueError(
            "Only the reviewed FE→BE:8080 policy is permitted by this demonstration executor"
        )


def main():
    p = argparse.ArgumentParser(description="정책 변경안 작성 및 내용 해시 승인 적용")
    p.add_argument("action", choices=["plan", "apply"])
    p.add_argument("--host", required=True)
    p.add_argument("--key", type=Path, required=True)
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--candidate", type=Path)
    p.add_argument("--approved-sha256")
    a = p.parse_args()
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*@[a-zA-Z0-9][a-zA-Z0-9.-]+", a.host):
        raise ValueError("Invalid host")

    def remote(cmd, body=None):
        r = subprocess.run(
            [
                "ssh",
                "-i",
                str(a.key.expanduser()),
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                a.host,
                "sudo -n k3s kubectl " + cmd,
            ],
            input=body,
            text=True,
            check=False,
            capture_output=True,
            timeout=30,
        )
        if r.returncode:
            raise RuntimeError("Kubernetes policy operation failed")
        return r.stdout

    current = remote(
        f"get networkpolicy {NAME} -n {NS} --ignore-not-found -o json"
    ).strip()
    revision = json.loads(current)["metadata"]["resourceVersion"] if current else None
    if a.action == "plan":
        candidate = (
            yaml.safe_load(a.candidate.read_text()) if a.candidate else expected()
        )
        validate(candidate)
        plan = {
            "schema_version": 1,
            "source": "submitted_candidate" if a.candidate else "reviewed_template",
            "prior_revision": revision,
            "candidate": candidate,
        }
        a.plan.write_text(json.dumps(plan, indent=2))
        print(hashlib.sha256(canonical(plan)).hexdigest())
        return
    plan = json.loads(a.plan.read_text())
    validate(plan["candidate"])
    if a.approved_sha256 != hashlib.sha256(canonical(plan)).hexdigest():
        raise ValueError("Approval does not match the reviewed plan")
    if revision != plan["prior_revision"]:
        raise ValueError("Policy changed since approval; create a new plan")
    candidate = plan["candidate"]
    if revision:
        candidate["metadata"]["resourceVersion"] = revision
        command = "replace -f -"
    else:
        command = "create -f -"
    print(remote(command, json.dumps(candidate)))


if __name__ == "__main__":
    main()
