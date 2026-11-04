"""Prepare only Delta's namespace and scoped, expiring credentials via admin SSH."""

import argparse
import base64
import json
import os
import re
import subprocess
from pathlib import Path

from render import NAMESPACE, resources


def main():
    parser = argparse.ArgumentParser(description="온프레미스 k3s 전용 실행 공간 준비")
    parser.add_argument("--host", required=True)
    parser.add_argument("--key", required=True, type=Path)
    parser.add_argument("--source", required=True, action="append")
    parser.add_argument("--node-name", required=True)
    parser.add_argument("--node-ip", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*@[a-zA-Z0-9][a-zA-Z0-9.-]+", args.host):
        raise SystemExit("Invalid SSH destination")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,62}", args.node_name):
        raise SystemExit("Invalid node name")
    manifest = resources(args.source)
    args.output.mkdir(parents=True, mode=0o700, exist_ok=False)

    def remote(command, data=None):
        result = subprocess.run(
            [
                "ssh",
                "-i",
                str(args.key.expanduser()),
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "ConnectTimeout=10",
                args.host,
                command,
            ],
            input=data,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode:
            # Commands may return tokens; never include command/output in an error.
            raise RuntimeError("SSH/k3s 작업 실패. 실행 공간 상태를 확인하세요.")
        return result.stdout

    def save(name, value):
        fd = os.open(args.output / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as out:
            out.write(value)

    node = json.loads(remote(f"sudo k3s kubectl get node {args.node_name} -o json"))
    if node["status"]["nodeInfo"]["architecture"] != "amd64":
        raise RuntimeError("현재 빌드는 amd64 노드가 필요합니다.")
    if not any(
        a["type"] == "InternalIP" and a["address"] == args.node_ip
        for a in node["status"]["addresses"]
    ):
        raise RuntimeError("노드 사설 IP 불일치")
    current = remote(
        f"sudo k3s kubectl get namespace {NAMESPACE} --ignore-not-found -o json"
    ).strip()
    if (
        current
        and json.loads(current)["metadata"]
        .get("labels", {})
        .get("app.kubernetes.io/managed-by")
        != "delta-platform"
    ):
        raise RuntimeError("기존 namespace를 변경하지 않았습니다.")
    all_services = json.loads(remote("sudo k3s kubectl get services -A -o json"))
    for service in all_services["items"]:
        if any(
            p.get("nodePort") == 32080 for p in service["spec"].get("ports", [])
        ) and (service["metadata"]["namespace"], service["metadata"]["name"]) != (
            NAMESPACE,
            "sandbox",
        ):
            raise RuntimeError("다른 서비스가 검사 포트를 사용 중입니다.")
    ns = json.dumps(manifest["items"][0])
    remote("sudo k3s kubectl apply --dry-run=server -f -", ns)
    remote("sudo k3s kubectl apply -f -", ns)
    body = json.dumps(manifest)
    remote("sudo k3s kubectl apply --dry-run=server -f -", body)
    save("apply.txt", remote("sudo k3s kubectl apply -f -", body))
    ca = remote("sudo cat /var/lib/rancher/k3s/server/tls/server-ca.crt")
    target = {
        "cloud": "onprem",
        "api_url": f"https://{args.node_ip}:6443",
        "ca_base64": base64.b64encode(ca.encode()).decode(),
        "namespace": NAMESPACE,
        "node_name": args.node_name,
        "node_ip": args.node_ip,
        "node_port": 32080,
        "registry_region": "ap-northeast-2",
        "allowed_image_prefixes": [args.repository + "@"],
    }
    save("target.json", json.dumps(target))
    for role in ("controller", "scanner"):
        token = remote(
            f"sudo k3s kubectl -n {NAMESPACE} create token {role} --duration=168h"
        ).strip()
        save(role + ".token", token)
    save("manifest.json", body)
    print(
        "전용 namespace 준비 완료. 제한된 권한과 7일 요청 토큰을 지정 디렉터리에 저장했습니다."
    )
    print("토큰 실제 만료와 AWS 사설 연결을 확인한 후 플랫폼을 활성화하세요.")


if __name__ == "__main__":
    main()
