"""Runs as root on a newly created VM; input is data, never a shell program."""

import base64
import hashlib
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
    install_compose()
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
    if payload.get("database_secret"):
        write_database_secret(root, payload["database_secret"])
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


def write_database_secret(root, reference):
    if reference["provider"] == "aws":
        response = json.loads(
            command(
                [
                    "aws",
                    "secretsmanager",
                    "get-secret-value",
                    "--region",
                    "ap-northeast-2",
                    "--secret-id",
                    reference["secret_id"],
                ]
            )
        )
        value = json.loads(response["SecretString"])
    elif reference["provider"] == "gcp":
        request = urllib.request.Request(
            "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
            headers={"Metadata-Flavor": "Google"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            token = json.load(response)["access_token"]
        request = urllib.request.Request(
            "https://secretmanager.googleapis.com/v1/"
            + reference["version"]
            + ":access",
            headers={"Authorization": "Bearer " + token},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            value = json.loads(base64.b64decode(json.load(response)["payload"]["data"]))
    else:
        raise ValueError("Unsupported database credential source")
    env = {
        "STORAGE_BACKEND": "postgres",
        "PGHOST": value["host"],
        "PGPORT": str(value.get("port", 5432)),
        "PGDATABASE": value["dbname"],
        "PGUSER": value["username"],
        "PGPASSWORD": value["password"],
        "PGSSLMODE": "verify-full",
        "PGSSLROOTCERT": "/run/delta-db/ca.pem",
    }
    if any(
        not isinstance(v, str) or any(c in v for c in "\r\n\x00") for v in env.values()
    ):
        raise ValueError("Invalid database environment")
    (root / "database.env").write_text(
        "".join(k + "=" + v + "\n" for k, v in env.items())
    )
    (root / "database.env").chmod(0o600)
    ca_dir = root / "db-ca"
    ca_dir.mkdir(exist_ok=True)
    ca_dir.chmod(0o755)
    (ca_dir / "ca.pem").write_text(value["ca_pem"])
    (ca_dir / "ca.pem").chmod(0o444)


def install_compose():
    plugin = Path("/usr/local/lib/docker/cli-plugins/docker-compose")
    expected = "7758048ad38e580f22559603312874fec8bb638d021a9e64182385fc17cc12a2"
    if plugin.exists() and hashlib.sha256(plugin.read_bytes()).hexdigest() == expected:
        return
    with urllib.request.urlopen(
        "https://github.com/docker/compose/releases/download/v5.7.0/docker-compose-linux-x86_64",
        timeout=60,
    ) as response:
        content = response.read(150_000_000)
    if hashlib.sha256(content).hexdigest() != expected:
        raise RuntimeError("Docker Compose checksum mismatch")
    plugin.parent.mkdir(parents=True, exist_ok=True)
    temporary = plugin.with_suffix(".tmp")
    temporary.write_bytes(content)
    temporary.chmod(0o755)
    temporary.replace(plugin)


def compose_document(payload):
    count = len(payload["components"])
    services = {}
    for index, component in enumerate(payload["components"]):
        name = "backend" if index else "frontend"
        env = dict(payload.get("runtime_env", {}).get(component["name"], {}))
        if not index and count == 2:
            env.update(API_HOST="backend", API_PORT="8080", API_PROTOCOL="http")
        item = {
            "image": payload["images"][index],
            "pull_policy": "never",
            "restart": "unless-stopped",
            "init": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "pids_limit": 128,
            "mem_limit": str(payload["memory_mb"] // count) + "m",
            "cpus": 1 / count,
            "environment": env,
            "ports": [
                f"127.0.0.1:8089:{component['container_port']}"
                if index
                else f"8088:{component['container_port']}"
            ],
            "logging": {
                "driver": "json-file",
                "options": {"max-size": "10m", "max-file": "2"},
            },
        }
        if count == 2:
            # Delta Notes template: Node is present in both immutable images.
            url = f"http://127.0.0.1:{component['container_port']}{component['healthcheck_path']}"
            item["healthcheck"] = {
                "test": [
                    "CMD",
                    "node",
                    "-e",
                    "fetch("
                    + json.dumps(url)
                    + ").then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))",
                ],
                "interval": "5s",
                "timeout": "4s",
                "retries": 20,
                "start_period": "10s",
            }
        if not index and count == 2:
            item["depends_on"] = {"backend": {"condition": "service_healthy"}}
        if index and payload.get("database_secret"):
            item["env_file"] = [
                {"path": "/opt/delta-app/database.env", "format": "raw"}
            ]
            item["volumes"] = [
                {
                    "type": "bind",
                    "source": "/opt/delta-app/db-ca",
                    "target": "/run/delta-db",
                    "read_only": True,
                }
            ]
        services[name] = item
    return {"name": "delta-app", "services": services}


def start():
    root = Path("/opt/delta-app")
    payload = json.loads((root / "runtime.json").read_text())
    path = root / "compose.json"
    path.write_text(json.dumps(compose_document(payload)))
    path.chmod(0o600)
    command(
        [
            "docker",
            "compose",
            "-f",
            str(path),
            "up",
            "-d",
            "--no-build",
            "--pull",
            "never",
            "--wait",
            "--wait-timeout",
            "180",
        ]
    )


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
