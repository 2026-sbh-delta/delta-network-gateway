"""Operator-owned setup and deployment, isolated from the team's AI resources."""

import argparse
import base64
import hashlib
import json
import os
import secrets
import shlex
import subprocess
import tempfile
import time
from pathlib import Path

import boto3


def prepare(ssm):
    name = "/delta-platform/ai/runtime"
    try:
        ssm.get_parameter(Name=name)
        print("Existing platform AI runtime retained")
        return
    except ssm.exceptions.ParameterNotFound:
        pass
    with tempfile.TemporaryDirectory(prefix="platform-ai-tls-") as directory:
        root = Path(directory)
        (root / "leaf.ext").write_text(
            "subjectAltName=IP:10.80.20.10\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n"
        )
        commands = [
            [
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "90",
                "-keyout",
                "ca.key",
                "-out",
                "ca.crt",
                "-subj",
                "/CN=Platform AI CA",
                "-addext",
                "basicConstraints=critical,CA:TRUE",
            ],
            [
                "req",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                "server.key",
                "-out",
                "server.csr",
                "-subj",
                "/CN=10.80.20.10",
            ],
            [
                "x509",
                "-req",
                "-in",
                "server.csr",
                "-CA",
                "ca.crt",
                "-CAkey",
                "ca.key",
                "-CAcreateserial",
                "-out",
                "server.crt",
                "-days",
                "30",
                "-extfile",
                "leaf.ext",
            ],
        ]
        for command in commands:
            subprocess.run(
                ["openssl", *command],
                cwd=root,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        runtime = {
            "env": {"DELTA_API_TOKEN": secrets.token_urlsafe(32), "DELTA_LLM": "none"},
            "tls": {
                name: base64.b64encode((root / name).read_bytes()).decode()
                for name in ("ca.crt", "server.crt", "server.key")
            },
        }
        ssm.put_parameter(
            Name=name,
            Value=json.dumps(runtime),
            Type="SecureString",
            Tier="Advanced",
            Overwrite=False,
        )
    print("Independent platform AI token and 30-day certificate prepared; LLM mode=none")


def deploy(session, outputs, image):
    if not image or not image.startswith(outputs["repository_url"] + "@sha256:"):
        raise ValueError("A pinned image from the platform AI repository is required")
    body = Path(__file__).with_name("install.py").read_bytes()
    checksum = hashlib.sha256(body).hexdigest()
    bucket = f"delta-platform-artifacts-{outputs['account_id']}"
    key = f"releases/ai/{checksum}/install.py"
    session.client("s3").put_object(
        Bucket=bucket, Key=key, Body=body, ServerSideEncryption="AES256"
    )
    path = "/opt/delta-platform-ai/install.py"
    commands = [
        "set -eu",
        shlex.join(
            [
                "aws",
                "s3",
                "cp",
                f"s3://{bucket}/{key}",
                path,
                "--region",
                outputs["region"],
                "--only-show-errors",
            ]
        ),
        f"printf '%s  %s\\n' {shlex.quote(checksum)} {shlex.quote(path)} | sha256sum -c -",
        shlex.join(
            [
                "python3",
                path,
                "--image",
                image,
                "--region",
                outputs["region"],
                "--account-id",
                outputs["account_id"],
            ]
        ),
    ]
    ssm = session.client("ssm")
    instance = outputs["instance_id"]
    command = ssm.send_command(
        InstanceIds=[instance],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": commands, "executionTimeout": ["600"]},
        Comment="Install independent platform AI service",
    )["Command"]["CommandId"]
    print("SSM command:", command, flush=True)
    for _ in range(120):
        time.sleep(5)
        try:
            result = ssm.get_command_invocation(CommandId=command, InstanceId=instance)
        except ssm.exceptions.InvocationDoesNotExist:
            continue
        if result["Status"] in ("Failed", "Cancelled", "TimedOut"):
            raise RuntimeError("AI installation failed; inspect SSM result with operator access")
        if result["Status"] == "Success":
            print("Installation succeeded; validate from platform before activating the connection")
            return
    raise TimeoutError("AI installation timed out")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("operation", choices=["prepare", "deploy"])
    p.add_argument("--profile", required=True)
    p.add_argument("--outputs", type=Path, required=True)
    p.add_argument("--image")
    args = p.parse_args()
    os.umask(0o077)
    outputs = {key: value["value"] for key, value in json.loads(args.outputs.read_text()).items()}
    session = boto3.Session(profile_name=args.profile, region_name=outputs["region"])
    if session.client("sts").get_caller_identity()["Account"] != outputs["account_id"]:
        raise ValueError("Account mismatch")
    if (
        outputs["runtime_parameter"] != "/delta-platform/ai/runtime"
        or outputs["private_ip"] != "10.80.20.10"
    ):
        raise ValueError("Only the independent platform AI environment may be changed")
    if args.operation == "prepare":
        prepare(session.client("ssm"))
    else:
        deploy(session, outputs, args.image)


if __name__ == "__main__":
    main()
