"""Bind existing app UIDs and issue a namespaced, resource-name-limited credential."""

import argparse
import base64
import ipaddress
import json
import os
import re
import subprocess
from pathlib import Path
from uuid import UUID


def access(namespace, account, resources):
    metadata = {
        "name": account,
        "namespace": namespace,
        "labels": {"app.kubernetes.io/managed-by": "delta-platform"},
    }
    rules = []
    for kind, group, collection in (
        ("Deployment", "apps", "deployments"),
        ("Service", "", "services"),
    ):
        names = [r["name"] for r in resources if r["kind"] == kind]
        if names:
            rules.append(
                {
                    "apiGroups": [group],
                    "resources": [collection],
                    "resourceNames": names,
                    "verbs": ["get", "delete"],
                }
            )
    return {
        "apiVersion": "v1",
        "kind": "List",
        "items": [
            {
                "apiVersion": "v1",
                "kind": "ServiceAccount",
                "metadata": metadata,
                "automountServiceAccountToken": False,
            },
            {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "Role",
                "metadata": metadata,
                "rules": rules,
            },
            {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "RoleBinding",
                "metadata": metadata,
                "roleRef": {
                    "apiGroup": "rbac.authorization.k8s.io",
                    "kind": "Role",
                    "name": account,
                },
                "subjects": [
                    {"kind": "ServiceAccount", "name": account, "namespace": namespace}
                ],
            },
        ],
    }


def main():
    p = argparse.ArgumentParser(
        description="기존 앱의 삭제 대상과 제한된 권한 등록; 앱 삭제는 하지 않음"
    )
    p.add_argument("--host", required=True)
    p.add_argument("--key", required=True, type=Path)
    p.add_argument("--node-ip", required=True)
    p.add_argument("--namespace", required=True)
    p.add_argument("--service-id", required=True, type=UUID)
    p.add_argument("--environment-id", required=True, type=UUID)
    p.add_argument("--binding-input", required=True, type=Path)
    p.add_argument(
        "--deployment", required=True, action="append", help="name=image@sha256:digest"
    )
    p.add_argument("--service", action="append", default=[])
    p.add_argument("--output", required=True, type=Path)
    a = p.parse_args()
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*@[a-zA-Z0-9][a-zA-Z0-9.-]+", a.host):
        raise ValueError("Invalid SSH destination")
    if not re.fullmatch(r"delta-[a-z0-9-]{1,50}", a.namespace):
        raise ValueError("Dedicated platform namespace required")
    ip = ipaddress.ip_address(a.node_ip)
    if ip.version != 4 or not ip.is_private or ip.is_link_local or ip.is_unspecified:
        raise ValueError("Private node IPv4 required")
    requested = []
    for spec in a.deployment:
        name, image = spec.split("=", 1)
        if not re.fullmatch(r"[a-zA-Z0-9./:_-]+@sha256:[a-f0-9]{64}", image):
            raise ValueError("Expected immutable app image")
        requested.append(("Deployment", name, image))
    requested.extend(("Service", name, None) for name in a.service)
    if len(requested) > 8 or len({(k, n) for k, n, _ in requested}) != len(requested):
        raise ValueError("At most eight unique resources")
    if any(not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", n) for _, n, _ in requested):
        raise ValueError("Invalid resource name")
    binding = json.loads(a.binding_input.read_text())
    if binding.get("cloud") != "onprem" or binding.get("runtime"):
        raise ValueError("Unbound on-premise environment input required")
    a.output.mkdir(parents=True, mode=0o700, exist_ok=False)
    ssh = [
        "ssh",
        "-i",
        str(a.key.expanduser()),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=8",
        a.host,
    ]

    def remote(command, body=None):
        r = subprocess.run(
            ssh + ["sudo -n k3s kubectl " + command],
            input=body,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if r.returncode:
            raise RuntimeError("온프레미스 등록 실패. 권한·기존 자원을 확인하세요.")
        return r.stdout

    def save(name, value):
        fd = os.open(a.output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as out:
            out.write(value)

    namespace = json.loads(remote(f"get namespace {a.namespace} -o json"))
    if (
        namespace["metadata"].get("labels", {}).get("app.kubernetes.io/managed-by")
        != "delta-platform"
    ):
        raise ValueError("Foreign namespace preserved")
    cluster = json.loads(remote("get namespace kube-system -o json"))["metadata"]["uid"]
    ownership = {
        "delta-platform/service-id": str(a.service_id),
        "delta-platform/environment-id": str(a.environment_id),
    }
    objects, resources = [], []
    for kind, name, image in requested:
        item = json.loads(remote(f"get {kind} {name} -n {a.namespace} -o json"))
        labels = item["metadata"].get("labels", {})
        if labels.get("app.kubernetes.io/managed-by") != "delta-platform" or any(
            labels.get(k) not in (None, v) for k, v in ownership.items()
        ):
            raise ValueError("Foreign resource preserved")
        if kind == "Deployment":
            pod = item["spec"]["template"]["spec"]
            containers = pod["containers"]
            if any(
                "persistentVolumeClaim" in v or "ephemeral" in v
                for v in pod.get("volumes", [])
            ):
                raise ValueError("Storage-backed application requires operator review")
            if len(containers) != 1 or containers[0]["image"] != image:
                raise ValueError("App image differs from the registered release")
        objects.append(item)
        resources.append({"kind": kind, "name": name, "uid": item["metadata"]["uid"]})
    for item in objects:
        if item["kind"] == "Service":
            selector = item["spec"].get("selector", {})
            if not selector or not any(
                d["kind"] == "Deployment"
                and all(
                    d["spec"]["template"]["metadata"]["labels"].get(k) == v
                    for k, v in selector.items()
                )
                for d in objects
            ):
                raise ValueError("Service must select a registered app Deployment")
    account = "remover-" + a.environment_id.hex
    manifest = access(a.namespace, account, resources)
    for obj in manifest["items"]:
        old = remote(
            f"get {obj['kind']} {account} -n {a.namespace} --ignore-not-found -o json"
        ).strip()
        if (
            old
            and json.loads(old)["metadata"]
            .get("labels", {})
            .get("app.kubernetes.io/managed-by")
            != "delta-platform"
        ):
            raise ValueError("Foreign access resource preserved")
    # Metadata-only updates do not restart the application Pods.
    for item in objects:
        payload = {
            "apiVersion": item["apiVersion"],
            "kind": item["kind"],
            "metadata": {
                "name": item["metadata"]["name"],
                "namespace": a.namespace,
                "uid": item["metadata"]["uid"],
                "resourceVersion": item["metadata"]["resourceVersion"],
                "labels": {**item["metadata"].get("labels", {}), **ownership},
            },
        }
        # kubectl patch reads the patch from stdin; the validated resource name is fixed.
        patch = {"metadata": payload["metadata"]}
        remote(
            f"patch {item['kind']} {item['metadata']['name']} -n {a.namespace} --type=merge --patch-file=/dev/stdin",
            json.dumps(patch),
        )
    remote("apply -f -", json.dumps(manifest))
    save("access.json", json.dumps(manifest, indent=2))
    ca = json.loads(
        remote("get configmap kube-root-ca.crt -n " + a.namespace + " -o json")
    )["data"]["ca.crt"]
    target = {
        "cluster_id": cluster,
        "api_url": f"https://{a.node_ip}:6443",
        "ca_base64": base64.b64encode(ca.encode()).decode(),
        "namespaces": [a.namespace],
    }
    binding["runtime"] = {
        "cluster_id": cluster,
        "namespace": a.namespace,
        "resources": resources,
    }
    save("binding.json", json.dumps(binding, indent=2, ensure_ascii=False))
    save("target.json", json.dumps(target))
    save(
        "runtime.token",
        remote(f"create token {account} -n {a.namespace} --duration=168h").strip(),
    )
    print("앱 소유 관계와 삭제 전용 권한을 준비했습니다. 앱·DB는 삭제하지 않았습니다.")
    print(
        "binding.json을 플랫폼 환경에 등록하고, 사설 연결·토큰 만료 확인 후 실행기를 활성화하세요."
    )


if __name__ == "__main__":
    main()
