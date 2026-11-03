"""Run on the existing tooling host. Never print its token or model credentials."""

import argparse
import base64
import http.client
import ipaddress
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path("/etc/delta-platform-sast")
BASE = Path("/opt/delta")


def write(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(value)


def prefix_setting(value):
    prefixes = value.split(",")
    if not prefixes or any(
        not p.startswith("https://github.com/")
        or not p.endswith("/")
        or any(c in p for c in ("\n", "\r", "@", "?", "#", " "))
        for p in prefixes
    ):
        raise ValueError("Expected GitHub organization prefixes ending in /")
    return value


def nginx_config(ip):
    ip = str(ipaddress.IPv4Address(ip))
    return f"""user nginx;
worker_processes 1;
pid /run/delta-platform-sast.pid;
error_log stderr warn;
events {{ worker_connections 128; }}
http {{
  access_log off;
  server_tokens off;
  client_max_body_size 64k;
  proxy_connect_timeout 5s;
  proxy_read_timeout 30s;
  proxy_set_header Host 127.0.0.1:8790;
  server {{
    listen {ip}:443 ssl;
    ssl_certificate {ROOT}/server.crt;
    ssl_certificate_key {ROOT}/server.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    location = /healthz {{
      limit_except GET {{ deny all; }}
      proxy_pass http://127.0.0.1:8790;
    }}
    location = /v1/sast/scans {{
      limit_except POST {{ deny all; }}
      proxy_pass http://127.0.0.1:8790;
    }}
    location ~ ^/v1/sast/scans/[a-f0-9-]+(/sarif)?$ {{
      limit_except GET {{ deny all; }}
      proxy_pass http://127.0.0.1:8790;
    }}
    location ~ ^/api/platform/runs/[a-f0-9-]+$ {{
      limit_except GET PUT {{ deny all; }}
      client_max_body_size 2m;
      proxy_set_header Host 127.0.0.1:8765;
      proxy_pass http://127.0.0.1:8765;
    }}
    location ~ ^/api/platform/runs/[a-f0-9-]+/cancel$ {{
      limit_except POST {{ deny all; }}
      proxy_set_header Host 127.0.0.1:8765;
      proxy_pass http://127.0.0.1:8765;
    }}
    location / {{ return 404; }}
  }}
}}
"""


class DockerConnection(http.client.HTTPConnection):
    def __init__(self):
        super().__init__("localhost", timeout=90)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect("/var/run/docker.sock")


def docker(method, path, body=None):
    conn = DockerConnection()
    try:
        conn.request(
            method,
            "/v1.43" + path,
            body=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        data = response.read()
        if response.status not in (200, 201, 204, 304):
            raise RuntimeError(f"Docker {method} {path} failed: HTTP {response.status}")
        return json.loads(data) if data else None
    finally:
        conn.close()


def replacement(existing, prefixes):
    if existing["HostConfig"]["NetworkMode"] != "host":
        raise ValueError("Expected the existing loopback SAST host network")
    env = dict(item.split("=", 1) for item in existing["Config"]["Env"])
    env["SAST_REPO_PREFIXES"] = prefix_setting(prefixes)
    return {
        **existing["Config"],
        "Image": existing["Image"],
        "Env": [f"{key}={value}" for key, value in env.items()],
        "HostConfig": existing["HostConfig"],
    }


def source_policy(prefixes, token):
    existing = docker("GET", "/containers/delta-sast/json")
    current = dict(x.split("=", 1) for x in existing["Config"]["Env"])
    if current.get("SAST_REPO_PREFIXES") == prefixes:
        write(BASE / "sast-runtime.env", "SAST_REPO_PREFIXES=" + prefixes + "\n")
        return
    request = urllib.request.Request(
        "http://127.0.0.1:8790/v1/sast/scans",
        headers={"Authorization": "Bearer " + token},
    )
    scans = json.load(urllib.request.urlopen(request, timeout=10))
    if any(scan["state"] == "running" for scan in scans):
        raise RuntimeError(
            "SAST has active scans; wait before changing its source policy"
        )
    # Preserve image, model auth, mounts, restart policy and limits. DAST is untouched.
    docker(
        "POST",
        "/containers/create?name=delta-sast-next",
        replacement(existing, prefixes),
    )
    old_renamed = new_renamed = False
    try:
        docker("POST", "/containers/delta-sast/stop?t=30")
        docker("POST", "/containers/delta-sast/rename?name=delta-sast-before-platform")
        old_renamed = True
        docker("POST", "/containers/delta-sast-next/rename?name=delta-sast")
        new_renamed = True
        docker("POST", "/containers/delta-sast/start")
        for _ in range(30):
            try:
                with urllib.request.urlopen(
                    "http://127.0.0.1:8790/healthz", timeout=2
                ) as r:
                    if r.status == 200:
                        break
            except OSError:
                pass
            time.sleep(2)
        else:
            raise RuntimeError("Replacement SAST failed readiness")
    except Exception:
        new_name = "delta-sast" if new_renamed else "delta-sast-next"
        docker("DELETE", "/containers/" + new_name + "?force=true")
        if old_renamed:
            docker(
                "POST", "/containers/delta-sast-before-platform/rename?name=delta-sast"
            )
        docker("POST", "/containers/delta-sast/start")
        raise
    docker("DELETE", "/containers/delta-sast-before-platform")
    write(BASE / "sast-runtime.env", "SAST_REPO_PREFIXES=" + prefixes + "\n")


def prepare_certificate(ip):
    if (ROOT / "server.crt").exists():
        subprocess.run(
            [
                "openssl",
                "x509",
                "-in",
                str(ROOT / "server.crt"),
                "-noout",
                "-checkip",
                ip,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                "openssl",
                "x509",
                "-in",
                str(ROOT / "server.crt"),
                "-noout",
                "-checkend",
                "86400",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        return
    with tempfile.TemporaryDirectory(prefix="delta-sast-ca-") as folder:
        root = Path(folder)
        (root / "leaf.ext").write_text(
            f"subjectAltName=IP:{ip}\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n"
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
                "/CN=Delta SAST Internal CA",
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
                "/CN=" + ip,
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
        for filename in ("ca.crt", "server.crt", "server.key"):
            write(ROOT / filename, (root / filename).read_text())


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ip", required=True)
    p.add_argument(
        "--repo-prefixes",
        default="https://github.com/2026-sbh-delta/,https://github.com/docker/",
    )
    p.add_argument("--export-connection", action="store_true")
    p.add_argument("--service", choices=["SAST", "DAST"], default="SAST")
    args = p.parse_args()
    ip = ipaddress.IPv4Address(args.ip)
    if not ip.is_private:
        raise ValueError("Private tooling address required")
    secrets = dict(
        line.split("=", 1)
        for line in (BASE / "secrets.env").read_text().splitlines()
        if "=" in line
    )
    if args.export_connection:
        connection = {
            "url": "https://" + str(ip),
            "token": secrets["DELTA_SAST_TOKEN"],
            "ca_base64": base64.b64encode((ROOT / "ca.crt").read_bytes()).decode(),
        }
        if args.service == "DAST":
            values = dict(
                line.split("=", 1)
                for line in (BASE / "dast-platform.env").read_text().splitlines()
                if line
            )
            connection["token"] = values["DAST_PLATFORM_TOKEN"]
            request = urllib.request.Request(
                "http://127.0.0.1:8765/api/platform/target",
                headers={"Authorization": "Bearer " + connection["token"]},
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                registered = json.load(response)
            configured = json.loads((BASE / "platform-sandbox.json").read_text())
            if registered != {
                "target_id": "platform-sandbox",
                "target_url": configured["origin"],
            }:
                raise ValueError("Redeploy DAST to load the configured platform target")
            connection["sandbox_url"] = registered["target_url"]
        # Operator wrapper captures this in memory, never in CI or terminal output.
        print(json.dumps(connection))
        return
    os.umask(0o077)
    ROOT.mkdir(mode=0o700, exist_ok=True)
    source_policy(prefix_setting(args.repo_prefixes), secrets["DELTA_SAST_TOKEN"])
    subprocess.run(
        ["dnf", "install", "-y", "nginx"], check=True, stdout=subprocess.DEVNULL
    )
    prepare_certificate(str(ip))
    write(ROOT / "nginx.conf", nginx_config(str(ip)))
    subprocess.run(
        ["/usr/sbin/nginx", "-t", "-c", str(ROOT / "nginx.conf")], check=True
    )
    write(
        Path("/etc/systemd/system/delta-platform-sast.service"),
        """[Unit]
Description=Private HTTPS endpoint for platform SAST requests
After=network-online.target docker.service
Wants=network-online.target
Requires=docker.service
[Service]
Type=simple
ExecStartPre=/usr/sbin/nginx -t -c /etc/delta-platform-sast/nginx.conf
ExecStart=/usr/sbin/nginx -c /etc/delta-platform-sast/nginx.conf -g "daemon off;"
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
[Install]
WantedBy=multi-user.target
""",
    )
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "delta-platform-sast.service"], check=True)
    subprocess.run(["systemctl", "restart", "delta-platform-sast.service"], check=True)
    print("Private SAST HTTPS installed; DAST was not restarted")


if __name__ == "__main__":
    main()
