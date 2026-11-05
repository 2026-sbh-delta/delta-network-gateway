"""Short-lived Google credentials for IAP management of private customer VMs."""

import ipaddress
import os
import signal
import socket
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import google.auth
from google.auth.transport.requests import Request


@contextmanager
def credentials_file(account):
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(Request())
    with tempfile.TemporaryDirectory(prefix="delta-gcp-ssh-") as root:
        token = Path(root) / "access-token"
        token.touch(mode=0o600)
        token.write_text(credentials.token)
        env = {
            k: v
            for k, v in os.environ.items()
            if k != "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"
        }
        yield ["--account=" + account, "--access-token-file=" + str(token)], env


def ssh(args, account, runner):
    # Explicit access-token-file avoids Cloud SDK treating AWS WIF as workforce SSH.
    with credentials_file(account) as (auth, env):
        return runner(["gcloud", "compute", "ssh", *args, *auth], timeout=1000, env=env)


def local_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def stop(process):
    # gcloud starts SSH and an IAP proxy child. Stop the whole isolated group.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


@contextmanager
def forward(output, selected, database_endpoint=None):
    """Forward only app health and an operator-approved DB endpoint through IAP.

    Database TLS still verifies the original Cloud SQL hostname end-to-end.
    No admin secret is sent to the VM and no local listener is exposed externally.
    """
    targets = {"health": ("127.0.0.1", 8088)}
    if database_endpoint:
        address = ipaddress.ip_address(database_endpoint["address"])
        if (
            address.version != 4
            or not any(
                address in ipaddress.ip_network(cidr)
                for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
            )
            or database_endpoint["port"] != 5432
        ):
            raise ValueError(
                "IAP database endpoint must be a private PostgreSQL address"
            )
        targets["database"] = (str(address), 5432)
    ports = {}
    for key in targets:
        port = local_port()
        while port in ports.values():
            port = local_port()
        ports[key] = port
    flags = [
        "-N",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
    ]
    for key, (address, port) in targets.items():
        flags.extend(["-L", f"127.0.0.1:{ports[key]}:{address}:{port}"])
    with credentials_file(selected["ssh_service_account"]) as (auth, env):
        args = [
            "gcloud",
            "compute",
            "ssh",
            output["instance_name"],
            "--project",
            selected["identity"]["gcp_project"],
            "--zone",
            output["zone"],
            "--tunnel-through-iap",
            "--quiet",
            *auth,
            "--",
            *flags,
        ]
        process = subprocess.Popen(
            args,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("GCP IAP management tunnel failed")
                try:
                    for port in ports.values():
                        with socket.create_connection(("127.0.0.1", port), timeout=1):
                            pass
                    break
                except OSError:
                    time.sleep(0.25)
            else:
                raise RuntimeError("GCP IAP management tunnel did not become ready")
            yield ports
        finally:
            stop(process)
