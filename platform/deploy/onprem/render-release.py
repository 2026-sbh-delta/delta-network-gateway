"""Render immutable runtime images from an actual sandbox result, never a mutable tag."""

import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "instrumentation"))
from profiles import endpoint, fingerprint, inject

ROOT = Path(__file__).parent / "manifests/runtime"


def render(result, *, preview=False, instrumentation="off", collector_endpoint=None):
    if instrumentation not in ("off", "nodejs"):
        raise ValueError("Supported instrumentation: off, nodejs")
    if instrumentation != "off":
        endpoint(collector_endpoint or "")
    elif collector_endpoint:
        raise ValueError("Collector endpoint requires instrumentation")
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
            if instrumentation != "off":
                pod = inject(
                    item["spec"]["template"]["spec"],
                    collector_endpoint,
                    "delta-notes-" + comp,
                    commit,
                    metrics=comp == "frontend",
                )
                item["spec"]["template"]["spec"] = pod
                item["spec"]["template"]["metadata"].setdefault(
                    "annotations", {}
                ).update(
                    {
                        "delta-platform/instrumentation": "nodejs",
                        "delta-platform/instrumentation-sha256": fingerprint(pod),
                    }
                )
    if instrumentation != "off":
        from urllib.parse import urlsplit

        host = urlsplit(collector_endpoint).hostname
        if host.endswith(".svc.cluster.local"):
            service, namespace, *_ = host.split(".")
            peer = {
                "namespaceSelector": {
                    "matchLabels": {"kubernetes.io/metadata.name": namespace}
                },
                "podSelector": {"matchLabels": {"app.kubernetes.io/name": service}},
            }
        else:
            import ipaddress

            ip = ipaddress.ip_address(host)
            peer = {
                "ipBlock": {"cidr": str(ip) + ("/32" if ip.version == 4 else "/128")}
            }
        objects.append(
            {
                "apiVersion": "networking.k8s.io/v1",
                "kind": "NetworkPolicy",
                "metadata": {
                    "name": "app-to-collector",
                    "namespace": "delta-notes",
                    "labels": {"app.kubernetes.io/managed-by": "delta-platform"},
                },
                "spec": {
                    "podSelector": {"matchLabels": {"app": "delta-notes"}},
                    "policyTypes": ["Egress"],
                    "egress": [
                        {"to": [peer], "ports": [{"protocol": "TCP", "port": 4318}]}
                    ],
                },
            }
        )
    else:
        # Applying an OFF release also revokes the formerly granted collector path.
        objects.append(
            {
                "apiVersion": "networking.k8s.io/v1",
                "kind": "NetworkPolicy",
                "metadata": {
                    "name": "app-to-collector",
                    "namespace": "delta-notes",
                    "labels": {"app.kubernetes.io/managed-by": "delta-platform"},
                },
                "spec": {
                    "podSelector": {"matchLabels": {"app": "delta-notes"}},
                    "policyTypes": ["Egress"],
                    "egress": [],
                },
            }
        )
    for item in objects:
        item["metadata"].setdefault("annotations", {}).update(
            {
                "delta-platform/release-sha256": result["release_sha256"],
                "delta-platform/security-status": "pending" if preview else "passed",
            }
        )
    return copy.deepcopy(objects)


def main():
    p = argparse.ArgumentParser(description="검증된 이미지의 운영 후보 YAML 생성")
    p.add_argument("--result", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--preview", action="store_true")
    p.add_argument("--instrumentation", choices=("off", "nodejs"), default="off")
    p.add_argument("--collector-endpoint")
    a = p.parse_args()
    a.output.write_text(
        yaml.safe_dump_all(
            render(
                json.loads(a.result.read_text()),
                preview=a.preview,
                instrumentation=a.instrumentation,
                collector_endpoint=a.collector_endpoint,
            ),
            sort_keys=False,
        )
    )


if __name__ == "__main__":
    main()
