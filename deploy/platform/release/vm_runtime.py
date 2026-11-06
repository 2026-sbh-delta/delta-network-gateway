"""Runs as root on a newly created VM; input is data, never a shell program.

동규님 platform/deploy/workloads/vm_runtime.py 이식. 변경: 컨테이너 로그를 awslogs/gcplogs로 보낸다,
리전을 요청에서 받는다, 실행 중인 두 컨테이너의 이미지(Config.Image→image ID→RepoDigests)와
FE→BE→DB /api/health를 VM 안에서 관측해 마지막 JSON 한 줄로 출력한다.
"""

import base64
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path("/opt/delta-app")
SERVICES = ("frontend", "backend")
HEALTH_KEYS = ("status", "storage", "version", "environment", "cloudProvider", "databaseVersionNum")
ECR_HOST = re.compile(r"[0-9]{12}\.dkr\.ecr\.([a-z0-9-]+)\.amazonaws\.com")


def command(args, *, body=None, timeout=300):
    result = subprocess.run(
        args, check=False, input=body, capture_output=True, text=True, timeout=timeout
    )
    if result.returncode:
        raise RuntimeError("VM runtime command failed")
    return result.stdout


def registry_login(config, image, cloud):
    registry = image.split("/", 1)[0]
    if cloud == "aws":
        match = ECR_HOST.fullmatch(registry)
        if not match:
            raise ValueError("AWS VM pulls only from ECR")
        # ECR 토큰은 이미지 레지스트리의 리전으로 받는다(교차 계정 저장소도 같은 방식).
        password = command(["aws", "ecr", "get-login-password", "--region", match.group(1)])
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
        ["docker", "--config", config, "login", registry, "--username", user, "--password-stdin"],
        body=password,
    )


def main(payload):
    ROOT.mkdir(mode=0o700, exist_ok=True)
    for _ in range(120):
        if Path("/var/lib/delta-bootstrap-ready").exists():
            break
        time.sleep(5)
    else:
        raise RuntimeError("VM bootstrap timeout")
    install_compose()
    # Refuse to alter a previously assigned VM.
    identity = ROOT / "environment-id"
    if identity.exists() and identity.read_text() != payload["environment_id"]:
        raise RuntimeError("VM ownership mismatch")
    identity.write_text(payload["environment_id"])
    compose_document(payload)  # 로그·이미지 설정을 pull 전에 검증한다.
    with tempfile.TemporaryDirectory(prefix="delta-pull-") as config:
        for image in payload["images"]:
            registry_login(config, image, payload["cloud"])
            command(["docker", "--config", config, "pull", image])
    if payload.get("database_secret"):
        write_database_secret(ROOT, payload["database_secret"], payload.get("region", "ap-northeast-2"))
    # Pinned images stay in the local cache; restart does not require expiring pull credentials.
    (ROOT / "runtime.json").write_text(json.dumps(payload))
    (ROOT / "runtime.json").chmod(0o600)
    shutil.copyfile(__file__, ROOT / "runtime.py")
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
    command(["systemctl", "restart", "delta-app.service"], timeout=600)
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
    observed = observe(payload)
    print(
        json.dumps(
            {
                "environment_id": payload["environment_id"],
                "images": observed["images"],
                "health": observed["health"],
                "healthy": True,
            },
            separators=(",", ":"),
        )
    )


def observe(payload, *, run=None, fetch=None):
    """실행 중 컨테이너가 요청한 digest 이미지로 떠 있는지 Docker 표준 JSON으로 확인한다."""
    run = run or command
    path = str(ROOT / "compose.json")
    images = {}
    for index, _component in enumerate(payload["components"]):
        name = SERVICES[1] if index else SERVICES[0]
        expected = payload["images"][index]
        ids = run(["docker", "compose", "-f", path, "ps", "-q", name]).split()
        if len(ids) != 1:
            raise RuntimeError("Unexpected container count")
        container = json.loads(run(["docker", "container", "inspect", ids[0]]))[0]
        if not container["State"]["Running"] or container["Config"]["Image"] != expected:
            raise RuntimeError("Container image mismatch")
        image = json.loads(run(["docker", "image", "inspect", expected]))[0]
        if image["Id"] != container["Image"] or expected not in image.get("RepoDigests", []):
            raise RuntimeError("Container digest mismatch")
        images[name] = expected
    health = {}
    if len(payload["components"]) == 2:
        fetch = fetch or _fetch_health
        for _ in range(30):
            value = fetch("http://127.0.0.1:8088/api/health")
            if isinstance(value, dict) and value.get("status") == "ok":
                health = {k: value[k] for k in HEALTH_KEYS if k in value}
                break
            time.sleep(2)
        else:
            raise RuntimeError("Application health is not ok")
    return {"images": images, "health": health}


def _fetch_health(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return json.loads(response.read(65536))
    except (OSError, ValueError):
        return None


def write_database_secret(root, reference, region="ap-northeast-2"):
    if reference["provider"] == "aws":
        response = json.loads(
            command(
                [
                    "aws",
                    "secretsmanager",
                    "get-secret-value",
                    "--region",
                    region,
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
    if reference["provider"] == "gcp":
        # 컨테이너는 메타데이터 서버(=GCP DNS) 접근이 막혀 Cloud SQL 사설 DNS 이름을 풀지 못한다.
        # 호스트에서 이름을 사설 IP 로 풀어 넘기고, TLS 인증서 확인에는 원래 DNS 이름을 쓴다.
        env["PGTLS_SERVERNAME"] = value["host"]
        env["PGHOST"] = socket.gethostbyname(value["host"].rstrip("."))
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


def logging_options(payload):
    """클라우드 로그 드라이버만 허용한다(json-file 로컬 보관 대신 중앙 수집)."""
    logging = payload.get("logging") or {}
    expected = "awslogs" if payload["cloud"] == "aws" else "gcplogs"
    options = logging.get("options")
    if (
        logging.get("driver") != expected
        or not isinstance(options, dict)
        or any(not isinstance(k, str) or not isinstance(v, str) for k, v in options.items())
        or options.get("mode") != "non-blocking"
    ):
        raise ValueError("Cloud logging driver required")
    if expected == "awslogs" and (
        not re.fullmatch(r"/delta/workload/[0-9a-f-]{36}", options.get("awslogs-group", ""))
        or options.get("awslogs-create-group") != "true"
        or not options.get("awslogs-region")
    ):
        raise ValueError("Invalid awslogs configuration")
    if expected == "gcplogs" and not options.get("gcp-project"):
        raise ValueError("Invalid gcplogs configuration")
    return {"driver": expected, "options": dict(options)}


def compose_document(payload):
    count = len(payload["components"])
    logging = logging_options(payload)
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
            "logging": logging,
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
    payload = json.loads((ROOT / "runtime.json").read_text())
    path = ROOT / "compose.json"
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
        ],
        timeout=300,
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
