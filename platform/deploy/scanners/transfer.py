"""One-time scanner data transfer. Run only via operator SSM on named hosts."""

import argparse
import hashlib
import http.client
import io
import json
import os
import shutil
import socket
import ssl
import subprocess
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path, PurePosixPath

BASE = Path("/opt/delta")
TLS = Path("/etc/delta-platform-scanner")


class Docker(http.client.HTTPConnection):
    def __init__(self):
        super().__init__("localhost", timeout=120)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(120)
        self.sock.connect("/var/run/docker.sock")


def docker(method, path, body=None):
    conn = Docker()
    try:
        conn.request(
            method,
            "/v1.43" + path,
            body=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"},
        )
        res = conn.getresponse()
        data = res.read()
        if res.status not in (200, 201, 204, 304):
            raise RuntimeError(f"Docker request failed: {res.status}")
        return json.loads(data) if data else None
    finally:
        conn.close()


def safe_members(members):
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or not (member.isdir() or member.isfile()):
            raise ValueError("Archive contains unsafe paths or links")
        yield member


def export(component, config):
    name = "delta-" + component
    meta = docker("GET", "/containers/" + name + "/json")
    env = dict(value.split("=", 1) for value in meta["Config"]["Env"])
    if component == "sast":
        req = urllib.request.Request(
            "http://127.0.0.1:8790/v1/sast/scans",
            headers={"Authorization": "Bearer " + env["DELTA_SAST_TOKEN"]},
        )
        records = json.load(urllib.request.urlopen(req, timeout=10))
        if any(r["state"] in ("queued", "running") for r in records):
            raise RuntimeError("Wait for active SAST scans")
        paths = ["sast-state", "sources"]
    else:
        records = json.load(urllib.request.urlopen("http://127.0.0.1:8765/api/runs", timeout=10))[
            "runs"
        ]
        if any(r.get("status") in ("pending", "running") for r in records):
            raise RuntimeError("Wait for active DAST scans")
        paths = ["dast-data", "dast-runs", "platform-sandbox.json", "dast-platform.env"]
    paths += ["secrets.env", "llm.env", "wif/config", "wif-refresh.sh"]
    inspected = docker("GET", "/images/" + meta["Image"] + "/json")
    prefix = (
        config["account_id"]
        + ".dkr.ecr."
        + config["region"]
        + ".amazonaws.com/delta-tooling/"
        + component
        + "@sha256:"
    )
    images = [d for d in inspected.get("RepoDigests", []) if d.startswith(prefix)]
    if len(images) != 1:
        raise ValueError("Expected one pinned team scanner image")
    manifest = {"Config": meta["Config"], "HostConfig": meta["HostConfig"], "image": images[0]}
    manifest["Config"]["Image"] = images[0]
    if manifest["HostConfig"]["NetworkMode"] != "host":
        raise ValueError("Expected the existing loopback scanner configuration")
    with tempfile.TemporaryDirectory(prefix="platform-scanner-export-") as directory:
        archive = Path(directory) / "data.tar.gz"
        docker("POST", "/containers/" + name + "/pause")
        try:
            with tarfile.open(archive, "w:gz") as tar:
                content = json.dumps(manifest).encode()
                info = tarfile.TarInfo("manifest.json")
                info.size, info.mode = len(content), 0o600
                tar.addfile(info, io.BytesIO(content))
                for path in paths:
                    if (BASE / path).exists():
                        tar.add(
                            BASE / path,
                            arcname="data/" + path,
                            filter=lambda m: m if (m.isfile() or m.isdir()) else None,
                        )
                for suffix in ("service", "timer"):
                    source = Path("/etc/systemd/system/delta-wif-refresh." + suffix)
                    if source.exists():
                        tar.add(source, arcname="systemd/" + source.name)
        finally:
            docker("POST", "/containers/" + name + "/unpause")
        data = archive.read_bytes()
        req = urllib.request.Request(
            config["upload_url"],
            data=data,
            method="PUT",
            headers={"x-amz-server-side-encryption": "AES256"},
        )
        with urllib.request.urlopen(req, timeout=180) as response:
            if response.status != 200:
                raise RuntimeError("Snapshot upload failed")
        print(
            json.dumps(
                {
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "bytes": len(data),
                    "records": len(records),
                    "image": images[0],
                }
            )
        )


def certificate(ip):
    TLS.mkdir(mode=0o700, exist_ok=True)
    if (TLS / "server.crt").exists():
        subprocess.run(
            ["openssl", "x509", "-in", str(TLS / "server.crt"), "-noout", "-checkip", ip],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        return
    with tempfile.TemporaryDirectory(prefix="scanner-ca-") as directory:
        root = Path(directory)
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
                "/CN=Platform Scanner CA",
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
            shutil.copyfile(root / filename, TLS / filename)
            os.chmod(TLS / filename, 0o600)


def restore(component, config):
    if not Path("/opt/delta-platform-scanner/bootstrap-ready").exists():
        raise ValueError("Dedicated scanner bootstrap required")
    subprocess.run(["mountpoint", "-q", str(BASE)], check=True)
    marker = BASE / ".platform-restored"
    if marker.exists():
        raise ValueError("Already restored; do not overwrite live scanner records")
    with urllib.request.urlopen(config["download_url"], timeout=180) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != config["sha256"]:
        raise ValueError("Snapshot checksum mismatch")
    with tempfile.TemporaryDirectory(prefix="platform-scanner-restore-") as directory:
        root = Path(directory)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            members = list(safe_members(tar.getmembers()))
            tar.extractall(root, members=members, filter="data")
        manifest = json.loads((root / "manifest.json").read_text())
        # A new protected data disk must not contain existing scanner data.
        if any(p.name not in ("lost+found",) for p in BASE.iterdir()):
            raise ValueError("Refusing to replace nonempty scanner data")
        shutil.copytree(root / "data", BASE, dirs_exist_ok=True)
        if (root / "systemd").exists():
            for p in (root / "systemd").iterdir():
                shutil.copyfile(p, Path("/etc/systemd/system") / p.name)
    for dirname in ("sast-state", "dast-data", "dast-runs"):
        if (BASE / dirname).exists():
            subprocess.run(["chown", "-R", "10001:10001", str(BASE / dirname)], check=True)
    if (BASE / "wif-refresh.sh").exists():
        os.chmod(BASE / "wif-refresh.sh", 0o700)
        for parent in ("wif", "wif/config", "wif/config/configs"):
            (BASE / parent).mkdir(parents=True, exist_ok=True)
            os.chmod(BASE / parent, 0o755)
        for path in (BASE / "wif/config").rglob("*"):
            if path.is_file():
                os.chmod(path, 0o644)
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(
            ["systemctl", "enable", "--now", "delta-wif-refresh.timer"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["systemctl", "start", "delta-wif-refresh.service"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    ip = {"sast": "10.80.30.10", "dast": "10.80.40.10"}[component]
    certificate(ip)
    payload = manifest["Config"]
    payload["Image"] = manifest["image"]
    env = dict(item.split("=", 1) for item in payload["Env"])
    host = manifest["HostConfig"]
    host["Memory"] = (1280 if component == "sast" else 3072) * 1024 * 1024
    host["RestartPolicy"] = {"Name": "unless-stopped", "MaximumRetryCount": 0}
    if component == "dast":
        import base64

        ca = base64.b64decode(config["sast_ca_base64"], validate=True)
        public_ca = Path(ssl.get_default_verify_paths().cafile).read_bytes()
        bundle = BASE / "platform-ca-bundle.pem"
        bundle.write_bytes(public_ca + b"\n" + ca)
        os.chmod(bundle, 0o644)
        env["DELTA_SAST_URL"] = "https://10.80.30.10"
        env["SSL_CERT_FILE"] = "/run/platform-ca-bundle.pem"
        host["Binds"] = [*host.get("Binds", []), str(bundle) + ":/run/platform-ca-bundle.pem:ro"]
    payload["Env"] = [f"{key}={value}" for key, value in env.items()]
    payload["HostConfig"] = host
    password = subprocess.check_output(
        ["aws", "ecr", "get-login-password", "--region", config["region"]]
    )
    subprocess.run(
        [
            "docker",
            "login",
            "--username",
            "AWS",
            "--password-stdin",
            manifest["image"].split("/")[0],
        ],
        input=password,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["docker", "pull", manifest["image"]],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    name = "delta-" + component
    docker("POST", "/containers/create?name=" + name, payload)
    docker("POST", "/containers/" + name + "/start")
    port = 8790 if component == "sast" else 8765
    health = "/healthz" if component == "sast" else "/api/sast/status"
    for _ in range(45):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{health}", timeout=5) as response:
                if response.status == 200:
                    break
        except OSError:
            pass
        time.sleep(2)
    else:
        raise RuntimeError("Restored scanner did not become ready")
    location = "location /" if component == "sast" else "location /api/platform/"
    extra = "" if component == "sast" else "location / { return 404; }"
    (TLS / "nginx.conf").write_text(f"""user nginx;
worker_processes 1;
pid /run/delta-platform-scanner.pid;
events {{ worker_connections 128; }}
http {{
 access_log off; error_log stderr warn; server_tokens off; client_max_body_size 10m;
 server {{
  listen {ip}:443 ssl;
  ssl_certificate {TLS}/server.crt; ssl_certificate_key {TLS}/server.key;
  ssl_protocols TLSv1.2 TLSv1.3;
  {location} {{ proxy_pass http://127.0.0.1:{port}; proxy_read_timeout 160s; proxy_set_header Host 127.0.0.1:{port}; }}
  {extra}
 }}
}}
""")
    service = Path("/etc/systemd/system/delta-platform-scanner.service")
    service.write_text(f"""[Unit]
Description=Private platform scanner HTTPS
After=docker.service network-online.target
Requires=docker.service
[Service]
ExecStartPre=/usr/sbin/nginx -t -c {TLS}/nginx.conf
ExecStart=/usr/sbin/nginx -c {TLS}/nginx.conf -g "daemon off;"
Restart=on-failure
[Install]
WantedBy=multi-user.target
""")
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(
        ["systemctl", "enable", "--now", "delta-platform-scanner.service"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    marker.write_text(config["sha256"])
    import base64

    print(
        json.dumps(
            {
                "component": component,
                "image": manifest["image"],
                "ca_base64": base64.b64encode((TLS / "ca.crt").read_bytes()).decode(),
            }
        )
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["export", "restore"])
    parser.add_argument("--component", choices=["sast", "dast"], required=True)
    parser.add_argument("--parameter", required=True)
    args = parser.parse_args()
    if not args.parameter.startswith("/delta-platform/scanners/transfers/"):
        raise ValueError("Expected a platform transfer parameter")
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
                args.parameter,
                "--with-decryption",
                "--query",
                "Parameter.Value",
                "--output",
                "text",
            ]
        )
    )
    if config["component"] != args.component:
        raise ValueError("Component mismatch")
    if args.operation == "export":
        export(args.component, config)
    else:
        restore(args.component, config)


if __name__ == "__main__":
    main()
