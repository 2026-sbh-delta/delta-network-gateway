"""Runs as root on a newly created VM; input is data, never a shell program."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


def command(args, *, body=None, timeout=300):
    result = subprocess.run(
        args, check=False, input=body, capture_output=True, text=True, timeout=timeout
    )
    if result.returncode:
        raise RuntimeError("VM runtime command failed")
    return result.stdout


def main(payload):
    root = Path("/opt/delta-app")
    root.mkdir(mode=0o700, exist_ok=True)
    for _ in range(120):
        if Path("/var/lib/delta-bootstrap-ready").exists():
            break
        time.sleep(5)
    else:
        raise RuntimeError("VM bootstrap timeout")
    # Refuse to alter a previously assigned VM.
    identity = root / "environment-id"
    if identity.exists() and identity.read_text() != payload["environment_id"]:
        raise RuntimeError("VM ownership mismatch")
    identity.write_text(payload["environment_id"])
    with tempfile.TemporaryDirectory(prefix="delta-pull-") as config:
        for image in payload["images"]:
            registry = image.split("/", 1)[0]
            if payload["cloud"] == "aws":
                password = command(
                    ["aws", "ecr", "get-login-password", "--region", "ap-northeast-2"]
                )
                user = "AWS"
            else:
                request = urllib.request.Request(
                    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
                    headers={"Metadata-Flavor": "Google"},
                )
                with urllib.request.urlopen(request, timeout=10) as response:
                    password = json.load(response)["access_token"]
                user = "oauth2accesstoken"
            command(
                [
                    "docker",
                    "--config",
                    config,
                    "login",
                    registry,
                    "--username",
                    user,
                    "--password-stdin",
                ],
                body=password,
            )
            command(["docker", "--config", config, "pull", image])
    # Pinned images stay in the local cache; restart does not require expiring pull credentials.
    (root / "runtime.json").write_text(json.dumps(payload))
    (root / "runtime.json").chmod(0o600)
    shutil.copyfile(__file__, root / "runtime.py")
    unit = """[Unit]
After=network-online.target docker.service delta-container-isolation.service
Requires=docker.service delta-container-isolation.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/python3 /opt/delta-app/runtime.py --start
[Install]
WantedBy=multi-user.target
"""
    Path("/etc/systemd/system/delta-app.service").write_text(unit)
    command(["systemctl", "daemon-reload"])
    command(["systemctl", "enable", "delta-app.service"])
    command(["systemctl", "restart", "delta-app.service"])
    for index, component in enumerate(payload["components"]):
        port = 8088 if index == 0 else 8089
        url = f"http://127.0.0.1:{port}{component['healthcheck_path']}"
        for _ in range(60):
            try:
                with urllib.request.urlopen(url, timeout=3) as response:
                    if response.status == 200:
                        break
            except (OSError, ValueError):
                time.sleep(2)
                continue
            time.sleep(2)
        else:
            raise RuntimeError("App readiness timeout")
    print(
        json.dumps(
            {
                "environment_id": payload["environment_id"],
                "images": payload["images"],
                "healthy": True,
            }
        )
    )


def start():
    payload = json.loads(Path("/opt/delta-app/runtime.json").read_text())
    try:
        command(["docker", "network", "inspect", "delta-app"])
    except RuntimeError:
        command(["docker", "network", "create", "delta-app"])
    # Start BE first; restart is confined to this new VM's pinned release.
    for index in reversed(range(len(payload["components"]))):
        component = payload["components"][index]
        name = "delta-backend" if index else "delta-app"
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
        port = (
            f"127.0.0.1:8089:{component['container_port']}"
            if index
            else f"8088:{component['container_port']}"
        )
        args = [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--restart",
            "unless-stopped",
            "--network",
            "delta-app",
            "--pull",
            "never",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "128",
            "--memory",
            str(payload["memory_mb"] // len(payload["components"])) + "m",
            "--cpus",
            str(1 / len(payload["components"])),
            "--log-opt",
            "max-size=10m",
            "--log-opt",
            "max-file=2",
            "-p",
            port,
        ]
        env = dict(payload.get("runtime_env", {}).get(component["name"], {}))
        if index == 0 and len(payload["components"]) == 2:
            env.update(API_HOST="delta-backend", API_PORT="8080", API_PROTOCOL="http")
        for key, value in env.items():
            args.extend(["--env", f"{key}={value}"])
        args.append(payload["images"][index])
        command(args)


if __name__ == "__main__":
    os.umask(0o077)
    try:
        if sys.argv[1:] == ["--start"]:
            start()
        else:
            main(json.load(sys.stdin))
    except Exception:  # noqa: BLE001 - redact provider/command bodies at the process boundary.
        print("VM deployment failed; inspect this deployment's VM.", file=sys.stderr)
        sys.exit(1)
