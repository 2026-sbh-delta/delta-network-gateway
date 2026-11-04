"""Render additive namespaced objects. Applying is an explicit separate step."""

import argparse
import ipaddress
import json

NAMESPACE = "delta-platform-sandbox"


def resources(sources, node_port=32080):
    if not sources or len(sources) > 8 or not 30000 <= node_port <= 32767:
        raise ValueError("Explicit scanner/worker addresses and NodePort required")
    for source in sources:
        ip = ipaddress.ip_address(source)
        if (
            ip.version != 4
            or not ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_unspecified
        ):
            raise ValueError("Only registered private IPv4 sources")

    def obj(kind, name, *, api="v1", **fields):
        return {
            "apiVersion": api,
            "kind": kind,
            "metadata": {
                "name": name,
                "namespace": NAMESPACE,
                "labels": {"app.kubernetes.io/managed-by": "delta-platform"},
            },
            **fields,
        }

    items = [
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": NAMESPACE,
                "labels": {
                    "app.kubernetes.io/managed-by": "delta-platform",
                    "pod-security.kubernetes.io/enforce": "restricted",
                    "pod-security.kubernetes.io/enforce-version": "v1.36",
                },
            },
        }
    ]
    for name in ("runtime", "controller", "scanner"):
        items.append(obj("ServiceAccount", name, automountServiceAccountToken=False))
    for name, rules in (
        (
            "controller",
            [
                {
                    "apiGroups": [""],
                    "resources": ["pods", "secrets"],
                    "verbs": ["get", "create", "delete"],
                },
                {
                    "apiGroups": [""],
                    "resources": ["services"],
                    "resourceNames": ["sandbox"],
                    "verbs": ["get"],
                },
            ],
        ),
        (
            "scanner",
            [
                {"apiGroups": [""], "resources": ["pods"], "verbs": ["get"]},
                {
                    "apiGroups": [""],
                    "resources": ["services"],
                    "resourceNames": ["sandbox"],
                    "verbs": ["get"],
                },
            ],
        ),
    ):
        items.append(obj("Role", name, api="rbac.authorization.k8s.io/v1", rules=rules))
        items.append(
            obj(
                "RoleBinding",
                name,
                api="rbac.authorization.k8s.io/v1",
                roleRef={
                    "apiGroup": "rbac.authorization.k8s.io",
                    "kind": "Role",
                    "name": name,
                },
                subjects=[
                    {"kind": "ServiceAccount", "name": name, "namespace": NAMESPACE}
                ],
            )
        )
    items += [
        obj(
            "ResourceQuota",
            "sandbox",
            spec={
                "hard": {
                    "pods": "1",
                    "secrets": "1",
                    "persistentvolumeclaims": "0",
                    "services": "1",
                    "requests.cpu": "500m",
                    "limits.cpu": "500m",
                    "requests.memory": "256Mi",
                    "limits.memory": "256Mi",
                    "requests.ephemeral-storage": "256Mi",
                    "limits.ephemeral-storage": "256Mi",
                }
            },
        ),
        obj(
            "NetworkPolicy",
            "sandbox",
            api="networking.k8s.io/v1",
            spec={
                "podSelector": {},
                "policyTypes": ["Ingress", "Egress"],
                "egress": [],
                "ingress": [
                    {
                        "from": [
                            {"ipBlock": {"cidr": source + "/32"}} for source in sources
                        ]
                    }
                ],
            },
        ),
        obj(
            "Service",
            "sandbox",
            spec={
                "type": "NodePort",
                "externalTrafficPolicy": "Local",
                "selector": {"app": "delta-platform-sandbox"},
                "ports": [
                    {
                        "name": "http",
                        "port": 80,
                        "targetPort": "http",
                        "nodePort": node_port,
                    }
                ],
            },
        ),
    ]
    return {"apiVersion": "v1", "kind": "List", "items": items}


def main():
    parser = argparse.ArgumentParser(
        description="기존 앱과 분리된 k3s 샌드박스 구성 출력"
    )
    parser.add_argument("--source", required=True, action="append")
    parser.add_argument("--node-port", type=int, default=32080)
    args = parser.parse_args()
    print(json.dumps(resources(args.source, args.node_port), indent=2))


if __name__ == "__main__":
    main()
