"""Install the platform-owned AI image. Run on its VM via SSM; never print secrets."""

import argparse
import base64
import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path("/etc/delta-platform-ai")


def write(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(value if isinstance(value, bytes) else value.encode())


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--image", required=True)
    p.add_argument("--region", required=True)
    p.add_argument("--account-id", required=True)
    args = p.parse_args()
    repo = f"{args.account_id}.dkr.ecr.{args.region}.amazonaws.com/delta-platform-ai"
    if not re.fullmatch(re.escape(repo) + r"@sha256:[a-f0-9]{64}", args.image):
        raise ValueError("Expected a pinned platform-owned AI image")
    if not Path("/opt/delta-platform-ai/bootstrap-ready").exists():
        raise ValueError("Bootstrap not ready")
    subprocess.run(["mountpoint", "-q", "/srv/delta-platform-ai"], check=True)
    config = json.loads(
        subprocess.check_output(
            [
                "aws",
                "ssm",
                "get-parameter",
                "--region",
                args.region,
                "--name",
                "/delta-platform/ai/runtime",
                "--with-decryption",
                "--query",
                "Parameter.Value",
                "--output",
                "text",
            ]
        )
    )
    env = config["env"]
    if set(env) - {"DELTA_API_TOKEN", "DELTA_LLM", "ANTHROPIC_API_KEY"}:
        raise ValueError("Unexpected AI environment keys")
    if not env.get("DELTA_API_TOKEN") or env.get("DELTA_LLM") not in ("none", "claude"):
        raise ValueError("Missing API token or invalid model mode")
    if env["DELTA_LLM"] == "claude" and not env.get("ANTHROPIC_API_KEY"):
        raise ValueError("Claude mode needs a separately provisioned platform key")
    if any("\n" in value or "\r" in value for value in env.values()):
        raise ValueError("Runtime values must be single line")
    ROOT.mkdir(mode=0o700, exist_ok=True)
    for name in ("ca.crt", "server.crt", "server.key"):
        write(ROOT / name, base64.b64decode(config["tls"][name], validate=True))
    write(ROOT / "runtime.env", "".join(f"{key}={value}\n" for key, value in env.items()))
    write(
        ROOT / "nginx.conf",
        f"""user nginx;
worker_processes 1;
pid /run/delta-platform-ai.pid;
events {{ worker_connections 128; }}
http {{
  access_log off;
  error_log stderr warn;
  server_tokens off;
  client_max_body_size 10m;
  server {{
    listen 10.80.20.10:443 ssl;
    ssl_certificate {ROOT}/server.crt;
    ssl_certificate_key {ROOT}/server.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    location / {{
      proxy_pass http://127.0.0.1:8090;
      proxy_connect_timeout 5s;
      proxy_read_timeout 160s;
      proxy_set_header Host 127.0.0.1:8090;
    }}
  }}
}}
""",
    )
    subprocess.run(["nginx", "-t", "-c", str(ROOT / "nginx.conf")], check=True)
    password = subprocess.check_output(
        ["aws", "ecr", "get-login-password", "--region", args.region]
    )
    subprocess.run(
        ["docker", "login", "--username", "AWS", "--password-stdin", repo.split("/")[0]],
        input=password,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(["docker", "pull", args.image], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(
        ["docker", "rm", "-f", "delta-platform-ai"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            "delta-platform-ai",
            "--restart",
            "unless-stopped",
            "--publish",
            "127.0.0.1:8090:8090",
            "--env-file",
            str(ROOT / "runtime.env"),
            "--volume",
            "/srv/delta-platform-ai:/app/var",
            "--memory",
            "768m",
            "--pids-limit",
            "256",
            "--read-only",
            "--tmpfs",
            "/tmp",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--log-opt",
            "max-size=10m",
            "--log-opt",
            "max-file=3",
            args.image,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    for _ in range(30):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8090/healthz", timeout=2) as r:
                if r.status == 200:
                    break
        except OSError:
            pass
        time.sleep(2)
    else:
        raise RuntimeError("AI container failed health check")
    write(
        Path("/etc/systemd/system/delta-platform-ai.service"),
        f"""[Unit]
Description=Platform-only AI HTTPS endpoint
After=network-online.target docker.service
Requires=docker.service
[Service]
ExecStartPre=/usr/sbin/nginx -t -c {ROOT}/nginx.conf
ExecStart=/usr/sbin/nginx -c {ROOT}/nginx.conf -g "daemon off;"
Restart=on-failure
[Install]
WantedBy=multi-user.target
""",
    )
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(
        ["systemctl", "enable", "delta-platform-ai.service"], check=True, stdout=subprocess.DEVNULL
    )
    subprocess.run(["systemctl", "restart", "delta-platform-ai.service"], check=True)
    write(ROOT / "image.txt", args.image + "\n")
    print(
        "AI installed; platform reachability and authenticated assessment still require verification"
    )


if __name__ == "__main__":
    main()
