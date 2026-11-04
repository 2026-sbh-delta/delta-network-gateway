"""Read-only inventory of the VM; no package installation or test attack traffic."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

AUDIT = r"""
import json, os, platform, shutil, subprocess
def run(command):
    try:
        p = subprocess.run(command, capture_output=True, text=True, timeout=20)
        return {"exit_code": p.returncode, "output": p.stdout[:24000], "error": p.stderr[:1000]}
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"error": type(e).__name__}
result = {
    "architecture": platform.machine(),
    "kernel": platform.release(),
    "cpu_count": os.cpu_count(),
    "memory": run(["free", "-m"]),
    "disk": run(["df", "-h", "/"]),
    "routes": run(["ip", "route"]),
    "addresses": run(["ip", "-brief", "address"]),
    "runtimes": {name: shutil.which(name) for name in ("k3s", "kubectl", "docker", "tailscale")},
}
if shutil.which("k3s"):
    for name, args in {
        "nodes": ["get", "nodes", "-o", "wide"],
        "pods": ["get", "pods", "-A", "-o", "wide"],
        "storage": ["get", "pvc", "-A"],
        "policies": ["get", "networkpolicies", "-A"],
        "services": ["get", "services", "-A", "-o", "wide"],
    }.items():
        result[name] = run(["sudo", "-n", "k3s", "kubectl"] + args)
elif shutil.which("docker"):
    result["containers"] = run(["docker", "ps", "--format", "{{.Names}} {{.Image}} {{.Status}}"])
print(json.dumps(result))
"""


def main():
    parser = argparse.ArgumentParser(description="온프레미스 VM 읽기 전용 사전 확인")
    parser.add_argument("--host", required=True)
    parser.add_argument("--key", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*@[a-zA-Z0-9][a-zA-Z0-9.-]+", args.host):
        raise SystemExit("user@hostname 형식이 필요합니다.")
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Use the user's verified known_hosts; never disable SSH host verification.
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
            "python3 -",
        ],
        input=AUDIT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode:
        raise SystemExit("SSH 확인 실패. 연결·호스트 키·읽기 권한을 확인하세요.")
    data = json.loads(result.stdout)
    fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as out:
        json.dump(data, out, ensure_ascii=False, indent=2)
    print("읽기 전용 현황을 지정 파일에 저장했습니다. 서버 설정은 바꾸지 않았습니다.")


if __name__ == "__main__":
    main()
