"""Run through SSM; the host reads its own SecureString, never command-line secrets."""

import argparse
import base64
import json
import os
import re
import subprocess
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    if not re.fullmatch(
        r"616723161368\.dkr\.ecr\.ap-northeast-2\.amazonaws\.com/delta-platform@sha256:[a-f0-9]{64}",
        args.image,
    ):
        raise ValueError("A pinned platform tooling image is required")
    os.umask(0o077)
    config = json.loads(
        subprocess.check_output(
            [
                "aws",
                "ssm",
                "get-parameter",
                "--region",
                "ap-northeast-2",
                "--name",
                "/delta-platform/deployment/agent",
                "--with-decryption",
                "--query",
                "Parameter.Value",
                "--output",
                "text",
            ]
        )
    )
    root = Path("/etc/delta")
    root.mkdir(exist_ok=True)
    root.chmod(0o755)
    files = {
        "ca.crt": base64.b64decode(config["ca_base64"]),
        "agent-secret": config["agent_secret"].encode(),
    }
    deployment = config["deployment"]
    for profile in deployment["databases"].values():
        if profile.get("server_mode") == "create" and profile["clouds"] == ["aws"]:
            profile["ca_pem"] = (
                urllib.request.urlopen(
                    "https://truststore.pki.rds.amazonaws.com/ap-northeast-2/ap-northeast-2-bundle.pem",
                    timeout=20,
                )
                .read()
                .decode()
            )
    files["deployment.json"] = json.dumps(deployment).encode()
    for name, value in files.items():
        path = root / name
        path.write_bytes(value)
        path.chmod(0o400)
        os.chown(path, 10001, 10001)
    state = Path("/var/lib/delta-deploy/state")
    state.mkdir(parents=True, exist_ok=True)
    os.chown(state, 10001, 10001)
    state.chmod(0o700)
    for name in ("agent", "tmp"):
        path = state.parent / name
        path.mkdir(exist_ok=True)
        os.chown(path, 10001, 10001)
        path.chmod(0o700)
    subprocess.run(
        ["dnf", "install", "-y", "docker"], check=True, stdout=subprocess.DEVNULL
    )
    subprocess.run(["systemctl", "enable", "--now", "docker"], check=True)
    password = subprocess.check_output(
        ["aws", "ecr", "get-login-password", "--region", "ap-northeast-2"]
    )
    subprocess.run(
        [
            "docker",
            "login",
            "--username",
            "AWS",
            "--password-stdin",
            args.image.split("/")[0],
        ],
        input=password,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["docker", "pull", args.image], check=True, stdout=subprocess.DEVNULL
    )
    subprocess.run(
        ["docker", "rm", "-f", "delta-deployer"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            "delta-deployer",
            "--restart",
            "unless-stopped",
            "--network",
            "host",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            "1536m",
            "--pids-limit",
            "256",
            "--log-opt",
            "max-size=10m",
            "--log-opt",
            "max-file=3",
            "--tmpfs",
            "/home/deployer:rw,nosuid,size=256m,uid=10001,gid=10001",
            "--volume",
            "/var/lib/delta-deploy/tmp:/tmp",
            "--volume",
            "/var/lib/delta-deploy/agent:/home/deployer/agent",
            "--volume",
            "/var/lib/delta-deploy/state:/var/lib/delta-deploy/state",
            "--volume",
            "/etc/delta:/etc/delta:ro",
            "--env",
            "JENKINS_URL=" + config["url"],
            args.image,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    print("Dedicated deployment agent installed")


if __name__ == "__main__":
    main()
