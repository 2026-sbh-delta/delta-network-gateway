"""Read only approved namespaces and whitelist fields before producing AI evidence."""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

# Reuse the team's pure fact adapter; do not run its expensive scanners here.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "services/delta-ai"))
from delta_ai.adapters import cluster_facts
from delta_ai.contract import AssessmentRequest

NAMESPACES = ("delta-platform-sandbox", "delta-notes")


def sanitize(item):
    meta = item.get("metadata", {})
    result = {
        "kind": item["kind"],
        "name": meta["name"],
        "namespace": meta.get("namespace"),
        "uid": meta.get("uid"),
        "generation": meta.get("generation"),
    }
    spec = item.get("spec", {})
    if item["kind"] in ("Pod", "Deployment", "StatefulSet"):
        pod = spec.get("template", {}).get("spec", spec)
        result["workload"] = {
            "replicas": spec.get("replicas"),
            "serviceAccountName": pod.get("serviceAccountName"),
            "automountServiceAccountToken": pod.get("automountServiceAccountToken"),
            "securityContext": pod.get("securityContext"),
            "containers": [
                {
                    k: c[k]
                    for k in ("name", "image", "resources", "securityContext", "ports")
                    if k in c
                }
                for c in pod.get("containers", [])
            ],
        }
        result["status"] = {
            k: item.get("status", {}).get(k)
            for k in ("phase", "readyReplicas", "availableReplicas")
        }
    elif item["kind"] in ("Service", "Ingress", "NetworkPolicy", "ResourceQuota"):
        result["spec"] = spec
    elif item["kind"] == "Role":
        result["rules"] = item.get("rules", [])
    elif item["kind"] == "RoleBinding":
        result["roleRef"], result["subjects"] = (
            item["roleRef"],
            item.get("subjects", []),
        )
    return result


def main():
    parser = argparse.ArgumentParser(description="비밀값 없는 온프레미스 AI 입력 수집")
    parser.add_argument("--host", required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--desired", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-inventory", type=Path)
    parser.add_argument("--service-id")
    args = parser.parse_args()
    if bool(args.source_inventory) != bool(args.service_id):
        raise ValueError(
            "Source inventory and registered service ID are required together"
        )
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*@[a-zA-Z0-9][a-zA-Z0-9.-]+", args.host):
        raise ValueError("Invalid SSH host")

    def read(command):
        return json.loads(
            subprocess.run(
                [
                    "ssh",
                    "-i",
                    str(args.key.expanduser()),
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "StrictHostKeyChecking=yes",
                    "-o",
                    "ConnectTimeout=8",
                    args.host,
                    "sudo -n k3s kubectl " + command,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout
        )

    actual = []
    raw_items = []
    for namespace in NAMESPACES:
        raw_items.extend(
            x
            for x in read(
                "get pods,deployments,statefulsets,services,ingresses,networkpolicies,resourcequotas,roles,rolebindings -n "
                + namespace
                + " -o json"
            )["items"]
        )
    actual = [sanitize(x) for x in raw_items]
    facts = cluster_facts(
        [x for x in raw_items if x["kind"] == "Ingress"],
        [x for x in raw_items if x["kind"] in ("Deployment", "StatefulSet")],
    )
    nodes = read("get nodes -o json")["items"]
    result = {
        "schema_version": 1,
        "collected_at": datetime.now(UTC).isoformat(),
        "source": "kubernetes_read_only",
        "desired_sha256": hashlib.sha256(args.desired.read_bytes()).hexdigest(),
        "nodes": [
            {
                "name": n["metadata"]["name"],
                "capacity": n["status"]["capacity"],
                "allocatable": n["status"]["allocatable"],
                "architecture": n["status"]["nodeInfo"]["architecture"],
            }
            for n in nodes
        ],
        "actual": actual,
        "ai_facts": asdict(facts),
        "capabilities": {
            "network_policy": "configured_not_a_probe_result",
            "rbac": "namespace_scoped",
            "managed_threat_detection": "unsupported",
            "high_availability": "unsupported_single_node",
            "observability": "requires_separate_delivery_evidence",
            "scanning": "external_api_required",
        },
    }
    if args.source_inventory:
        request = AssessmentRequest.model_validate(
            {
                "assessment_id": "onprem-parity-" + args.service_id,
                "kind": "migration_parity",
                "service": {"service_id": args.service_id},
                "source_env": json.loads(args.source_inventory.read_text()),
                "target_env": {
                    "provider": "onprem",
                    "secrets": {
                        "source": facts.secrets_source,
                        "plaintext_env": facts.plaintext_env,
                    },
                    "ingress": {"tls": facts.ingress_tls},
                },
            }
        )
        result["assessment_request"] = request.model_dump(
            mode="json", exclude_none=True
        )
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(
        "환경 상태를 저장했습니다. Secret·환경변수 값·kubeconfig는 포함하지 않습니다."
    )


if __name__ == "__main__":
    main()
