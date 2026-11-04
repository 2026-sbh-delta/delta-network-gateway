"""Validate real Collector configs and mTLS boundaries locally; no cloud writes."""

import json
import os
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "observability"
IMAGE = "otel/opentelemetry-collector-contrib:0.164.0@sha256:340c9ca6a36617084c6661bdc4f0fe92b7cda5a805d61673ec8db375fddc522b"


def command(args, **kwargs):
    return subprocess.run(
        args, check=True, capture_output=True, text=True, **kwargs
    ).stdout.strip()


def certs(path):
    def openssl(*args):
        command(["openssl", *args], cwd=path)

    for name in ("server", "app", "host"):
        (path / "ca.cnf").write_text(
            "[req]\ndistinguished_name=dn\nx509_extensions=ca\n[dn]\n[ca]\n"
            "basicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\n"
            "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid:always\n"
        )
        openssl(
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            f"/CN=local-test-{name}-ca",
            "-keyout",
            f"{name}-ca-key.pem",
            "-out",
            f"{name}-ca.pem",
            "-config",
            "ca.cnf",
        )
        leaf = "server" if name == "server" else name + "-client"
        openssl(
            "req",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            f"/CN={leaf}",
            "-keyout",
            f"{leaf}-key.pem",
            "-out",
            f"{leaf}.csr",
        )
        usage = "serverAuth" if name == "server" else "clientAuth"
        (path / "extensions").write_text(
            f"extendedKeyUsage={usage}\nsubjectAltName=DNS:localhost,IP:127.0.0.1\n"
            "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
            "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n"
        )
        openssl(
            "x509",
            "-req",
            "-in",
            f"{leaf}.csr",
            "-CA",
            f"{name}-ca.pem",
            "-CAkey",
            f"{name}-ca-key.pem",
            "-CAcreateserial",
            "-days",
            "1",
            "-extfile",
            "extensions",
            "-out",
            f"{leaf}.pem",
        )
    # Only temporary test material, readable by the caller and the matching container UID.
    for file in path.iterdir():
        file.chmod(0o600)


def main():
    with tempfile.TemporaryDirectory(prefix="delta-otel-test-") as folder:
        private = Path(folder)
        certs(private)
        override = {
            "exporters": {"debug": {"verbosity": "detailed"}},
            "service": {
                "extensions": [],
                "telemetry": {"logs": {"level": "info"}},
                "pipelines": {
                    k: {"exporters": ["debug"]}
                    for k in ("metrics", "metrics/host", "logs", "traces")
                },
            },
            "processors": {"batch": {"timeout": "1s"}},
        }
        (private / "debug.json").write_text(json.dumps(override))
        env = {
            "AWS_REGION": "ap-northeast-2",
            "AWS_EC2_METADATA_DISABLED": "true",
            "AWS_ACCESS_KEY_ID": "local-test-only",
            "AWS_SECRET_ACCESS_KEY": "local-test-only",
            "DELTA_PROJECT_ID": "496562be-f67b-5f7e-a16b-929fd347b9ad",
            "DELTA_SERVICE_ID": "11111111-1111-4111-8111-111111111111",
            "DELTA_ENVIRONMENT_ID": "22222222-2222-4222-8222-222222222222",
            "DELTA_HOST_ID": "onprem-" + "a" * 32,
            "DELTA_HOST_GATEWAY": "https://127.0.0.1:4319",
            "DELTA_APP_GATEWAY": "https://127.0.0.1:4318",
            "DELTA_APP_LISTEN": "127.0.0.1:4318",
            "HOST_ROOT": "/",
        }
        base = [
            "docker",
            "run",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--memory=192m",
            "--cpus=0.5",
            "-v",
            f"{ROOT}:/config:ro",
            "-v",
            f"{private}:/etc/delta-otel:ro",
        ]
        for key, value in env.items():
            base += ["-e", f"{key}={value}"]
        gateway = [
            "--config=/config/collector.yaml",
            "--config=/config/onprem/gateway-overlay.yaml",
        ]
        for config in (
            ["--config=/config/onprem/host.yaml"],
            ["--config=/config/onprem/app.yaml"],
            gateway,
        ):
            command([*base, "--rm", "--network=none", IMAGE, "validate", *config])
        print("Collector configs: 3 validated")
        container = command(
            [
                *base,
                "-d",
                "-p",
                "127.0.0.1::4318",
                "-p",
                "127.0.0.1::4319",
                IMAGE,
                *gateway,
                "--config=/etc/delta-otel/debug.json",
            ]
        )
        try:
            ports = json.loads(command(["docker", "inspect", container]))[0][
                "NetworkSettings"
            ]["Ports"]
            urls = {
                k: "https://127.0.0.1:"
                + ports[f"{p}/tcp"][0]["HostPort"]
                + "/v1/metrics"
                for k, p in (("app", 4318), ("host", 4319))
            }
            contexts = {}
            for name in ("app", "host", "anonymous"):
                ctx = ssl.create_default_context(cafile=str(private / "server-ca.pem"))
                if name != "anonymous":
                    ctx.load_cert_chain(
                        str(private / f"{name}-client.pem"),
                        str(private / f"{name}-client-key.pem"),
                    )
                contexts[name] = ctx

            def post(destination, identity, name):
                payload = {
                    "resourceMetrics": [
                        {
                            "resource": {
                                "attributes": [
                                    {
                                        "key": "host.id",
                                        "value": {"stringValue": "forged-host"},
                                    },
                                    {
                                        "key": "delta_service_id",
                                        "value": {"stringValue": "forged-service"},
                                    },
                                ]
                            },
                            "scopeMetrics": [
                                {
                                    "metrics": [
                                        {
                                            "name": name,
                                            "gauge": {
                                                "dataPoints": [
                                                    {
                                                        "timeUnixNano": str(
                                                            time.time_ns()
                                                        ),
                                                        "asDouble": 0.25,
                                                        "attributes": [
                                                            {
                                                                "key": "state",
                                                                "value": {
                                                                    "stringValue": "idle"
                                                                },
                                                            }
                                                        ],
                                                    }
                                                ]
                                            },
                                        }
                                    ]
                                }
                            ],
                        }
                    ]
                }
                request = urllib.request.Request(
                    urls[destination],
                    data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(
                    request, context=contexts[identity], timeout=3
                ) as response:
                    assert response.status == 200

            deadline = time.monotonic() + 15
            while True:
                try:
                    post("app", "app", "delta.app.up")
                    break
                except (OSError, urllib.error.URLError):
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.3)
            for destination, identity in (
                ("app", "anonymous"),
                ("host", "anonymous"),
                ("host", "app"),
                ("app", "host"),
            ):
                try:
                    post(destination, identity, "system.cpu.utilization")
                except (OSError, urllib.error.URLError):
                    pass
                else:
                    raise AssertionError("Unauthorized certificate accepted")
            post(
                "app", "app", "system.cpu.utilization"
            )  # accepted OTLP, dropped by app filter
            post("host", "host", "system.cpu.utilization")
            time.sleep(2)
            logs = subprocess.run(
                ["docker", "logs", container],
                capture_output=True,
                text=True,
                check=True,
            )
            output = logs.stdout + logs.stderr
            assert "Name: delta.app.up" in output
            assert output.count("Name: system.cpu.utilization") == 1
            assert "forged-host" not in output and "forged-service" not in output
            assert env["DELTA_HOST_ID"] in output
            print(
                "mTLS: anonymous/cross-client rejected; identity overwritten; app host-metric spoof rejected"
            )
        except Exception:
            # Only this isolated fixture's state/logs; no production credentials are used.
            state = subprocess.run(
                ["docker", "inspect", "--format", "{{json .State}}", container],
                capture_output=True,
                text=True,
                check=False,
            )
            logs = subprocess.run(
                ["docker", "logs", "--tail", "30", container],
                capture_output=True,
                text=True,
                check=False,
            )
            print("Test collector state:", state.stdout.strip(), flush=True)
            print("Test collector logs:", logs.stdout + logs.stderr, flush=True)
            raise
        finally:
            command(["docker", "rm", "-f", container])
    print("Temporary collector and test credentials removed; AWS writes: none")


if __name__ == "__main__":
    main()
