"""Obtain an ECR pull token using the platform VM role; SSM output is encrypted."""

import argparse
import base64
import json
import os
import re
import shlex
import subprocess
import tempfile
import time
from pathlib import Path

import boto3


def main():
    p = argparse.ArgumentParser(description="플랫폼의 이미지 읽기 역할로 ECR 인증 발급")
    p.add_argument("--profile", required=True)
    p.add_argument("--instance", required=True)
    p.add_argument("--region", default="ap-northeast-2")
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--sync-host", help="선택: delta-notes의 registry Secret만 갱신")
    p.add_argument("--key", type=Path)
    a = p.parse_args()
    if a.sync_host and (
        not a.key
        or not re.fullmatch(r"[a-z_][a-z0-9_-]*@[a-zA-Z0-9][a-zA-Z0-9.-]+", a.sync_host)
    ):
        raise ValueError("Valid SSH host and key required")
    if not re.fullmatch(r"i-[a-f0-9]{17}", a.instance) or not re.fullmatch(
        r"[a-z0-9-]+", a.region
    ):
        raise ValueError("Invalid platform instance or region")
    ssm = boto3.Session(profile_name=a.profile, region_name=a.region).client("ssm")
    with tempfile.TemporaryDirectory(prefix="delta-pull-auth-") as tmp:
        folder = Path(tmp)
        key, cert = folder / "key.pem", folder / "cert.pem"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-subj",
                "/CN=delta-pull-auth",
                "-keyout",
                str(key),
                "-out",
                str(cert),
            ],
            check=True,
            capture_output=True,
        )
        key.chmod(0o600)
        program = (
            "import boto3,json; "
            "print(json.dumps({'principal':boto3.client('sts').get_caller_identity()['Arn'],"
            f"'auth':boto3.client('ecr',region_name={a.region!r}).get_authorization_token()['authorizationData'][0]}}"
            ",default=str))"
        )
        script = "\n".join(
            [
                "set -euo pipefail",
                "umask 077",
                "delta_cert=$(mktemp)",
                "trap 'rm -f \"$delta_cert\"' EXIT",
                "printf '%s' "
                + shlex.quote(base64.b64encode(cert.read_bytes()).decode())
                + ' | base64 -d > "$delta_cert"',
                "sudo docker exec delta-platform-worker-1 python -c "
                + shlex.quote(program)
                + ' | openssl cms -encrypt -binary -outform DER -aes-256-cbc "$delta_cert" | base64 -w0',
            ]
        )
        command = ssm.send_command(
            InstanceIds=[a.instance],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": ["bash -c " + shlex.quote(script)]},
            TimeoutSeconds=60,
        )["Command"]["CommandId"]
        response = {}
        for _ in range(60):
            try:
                response = ssm.get_command_invocation(
                    CommandId=command, InstanceId=a.instance
                )
            except ssm.exceptions.InvocationDoesNotExist:
                time.sleep(1)
                continue
            if response["Status"] not in ("Pending", "InProgress", "Delayed"):
                break
            time.sleep(1)
        if response.get("Status") != "Success":
            raise RuntimeError("Platform pull-role token request failed")
        sealed = base64.b64decode(response["StandardOutputContent"], validate=True)
        plain = subprocess.run(
            [
                "openssl",
                "cms",
                "-decrypt",
                "-binary",
                "-inform",
                "DER",
                "-recip",
                str(cert),
                "-inkey",
                str(key),
            ],
            input=sealed,
            capture_output=True,
            check=True,
        ).stdout
        value = json.loads(plain)
        if ":assumed-role/delta-platform-app/" not in value["principal"]:
            raise ValueError("Unexpected registry credential issuer")
        fd = os.open(a.output, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as out:
            json.dump(value["auth"], out)
    if a.sync_host:
        ssh = [
            "ssh",
            "-i",
            str(a.key.expanduser()),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            a.sync_host,
        ]
        old = subprocess.run(
            ssh + ["sudo -n k3s kubectl get secret registry -n delta-notes -o json"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        item = json.loads(old.stdout)
        if (
            item["metadata"].get("labels", {}).get("app.kubernetes.io/managed-by")
            != "delta-platform"
        ):
            raise ValueError("Foreign registry Secret preserved")
        auth = value["auth"]
        registry = auth["proxyEndpoint"].removeprefix("https://")
        config = json.dumps({"auths": {registry: {"auth": auth["authorizationToken"]}}})
        item = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": "registry",
                "namespace": "delta-notes",
                "resourceVersion": item["metadata"]["resourceVersion"],
                "labels": {"app.kubernetes.io/managed-by": "delta-platform"},
            },
            "type": "kubernetes.io/dockerconfigjson",
            "data": {".dockerconfigjson": base64.b64encode(config.encode()).decode()},
        }
        applied = subprocess.run(
            ssh + ["sudo -n k3s kubectl replace -f -"],
            input=json.dumps(item),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if applied.returncode:
            raise RuntimeError("Registry Secret refresh failed")
    print(
        "플랫폼 읽기 역할의 ECR 인증을 제한된 로컬 파일에 저장했습니다. SSM 출력은 암호문입니다."
    )


if __name__ == "__main__":
    main()
