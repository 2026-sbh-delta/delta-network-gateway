"""Apply four reviewed modules to the pinned demo Diff, preserving its config.

Run on the Diff host with a release directory and manifest. This never calls an
orchestrator run, Jenkins, a cloud provisioning API, or a DNS write API.
"""

import copy
import fcntl
import hashlib
import http.client
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request

SERVICE = "delta-diff"
ROOT = Path("/opt/delta/diff-cp")
MODULES = "/src/delta-diff/delta_diff"
PYTHON = "/src/delta-diff/.venv/bin/python"
BASE = "616723161368.dkr.ecr.ap-northeast-2.amazonaws.com/delta-cp-diff@sha256:07931d6e8a69db98a40a6142742f0feda55823444c93632ffd3903cef6eea2e7"
EXPECTED = {
    "diff.py": "687cf1cce5f22867f5e06795dc69091d4be2223e14b53194beca43a205bf1fcd",
    "terraform.py": "937d00ce8e7a119d5f07f4e0ecf49bcafc01356604b19c459e9b21e60f96f549",
    "contract.py": "f0be3a04309abe4e2c7070f7681f95cbb59b2eaf9e1f4bb16c3b9f055dc00974",
}


class DockerSocket(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(20)
        self.sock.connect("/var/run/docker.sock")


def engine(method, path, payload=None):
    conn = DockerSocket("localhost", timeout=20)
    try:
        conn.request(method, path, None if payload is None else json.dumps(payload), {"Content-Type": "application/json"})
        response = conn.getresponse()
        raw = response.read()
        if response.status not in (200, 201):
            raise RuntimeError(f"Docker Engine {response.status}; details suppressed")
        return json.loads(raw)
    finally:
        conn.close()


def docker(*args, timeout=40):
    process = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if process.returncode:
        raise RuntimeError(f"docker {args[0]} failed; details suppressed")
    return process.stdout.strip()


def inspect(name):
    p = subprocess.run(["docker", "inspect", "--type", "container", name], capture_output=True, text=True, timeout=10)
    return json.loads(p.stdout)[0] if not p.returncode else None


def hashes(name, names):
    code = "import json,hashlib,pathlib,sys; r=pathlib.Path(sys.argv[1]); print(json.dumps({n:hashlib.sha256((r/n).read_bytes()).hexdigest() for n in json.loads(sys.argv[2])}))"
    return json.loads(docker("exec", name, PYTHON, "-c", code, MODULES, json.dumps(list(names))))


def create(old, name, directory, manifest, candidate=False):
    config, host = copy.deepcopy(old["Config"]), copy.deepcopy(old["HostConfig"])
    config.pop("Hostname", None)
    config["Labels"] = {**(config.get("Labels") or {}), "delta.diff.iam-scope": manifest["release"],
                        "delta.diff.demo-scope": manifest["files"]["diff.py"]}
    destinations = {f"{MODULES}/{n}" for n in manifest["files"]}
    host["Binds"] = [b for b in host.get("Binds", []) if b.split(":")[1] not in destinations]
    host["Binds"].extend(f"{directory / n}:{MODULES}/{n}:ro" for n in manifest["files"])
    if candidate:
        cmd = config["Cmd"]
        cmd[cmd.index("--host") + 1] = "127.0.0.1"
        cmd[cmd.index("--port") + 1] = "18095"
        config["Healthcheck"]["Test"] = [s.replace("8095", "18095") for s in config["Healthcheck"]["Test"]]
        host["RestartPolicy"] = {"Name": "no", "MaximumRetryCount": 0}
    config["HostConfig"] = host
    version = engine("GET", "/version")["ApiVersion"]
    return engine("POST", f"/v{version}/containers/create?" + urllib.parse.urlencode({"name": name}), config)["Id"]


def ready(name, port, directory, manifest):
    for _ in range(30):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as r:
                if json.load(r).get("service") == "delta-diff":
                    break
        except OSError:
            pass
        time.sleep(1)
    else:
        raise RuntimeError("Diff health did not become ready")
    if hashes(name, manifest["files"]) != manifest["files"]:
        raise RuntimeError("live module hashes differ")
    code = (directory / "verify.py").read_text()
    # Credentials remain in the container; the verifier prints only fixed evidence fields.
    output = docker("exec", "-e", "PYTHONDONTWRITEBYTECODE=1", name, PYTHON, "-c", code, str(port), timeout=100)
    print(output, flush=True)


def main(directory):
    manifest = json.loads((directory / "manifest.json").read_text())
    assert set(manifest["files"]) == {"diff.py", "terraform.py", "contract.py", "iam.py"}
    for name, expected in manifest["files"].items():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == expected
    lock = (ROOT / "demo-scope.deploy.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    old = inspect(SERVICE)
    if (not old or not old["State"]["Running"] or old["Config"]["Image"] != BASE
            or old["HostConfig"]["NetworkMode"] != "host" or old["HostConfig"].get("Mounts")
            or hashes(SERVICE, EXPECTED) != EXPECTED):
        raise RuntimeError("live Diff changed; do not overwrite another session")
    env = dict(s.split("=", 1) for s in old["Config"].get("Env", []) if "=" in s)
    if env.get("DELTA_DIFF_DEMO_WORKLOAD_SCOPE") != "1":
        raise RuntimeError("the approved demo scope is not enabled")
    suffix = manifest["release"]
    candidate, next_name, backup = (f"{SERVICE}-{n}-{suffix}" for n in ("candidate", "next", "before-iam-lambda"))
    if any(inspect(n) for n in (candidate, next_name, backup)):
        raise RuntimeError("existing candidate or backup needs review")
    state = next((m["Source"] for m in old["Mounts"] if m["Destination"] == "/state"), None)
    if not state:
        raise RuntimeError("persistent state mount unavailable")
    shutil.copytree(state, directory / "state-before", symlinks=True)
    (directory / "state-before").chmod(0o700)
    next_id = None
    stopped = committed = False
    try:
        create(old, candidate, directory, manifest, candidate=True)
        docker("start", candidate)
        ready(candidate, 18095, directory, manifest)
        docker("rm", "-f", candidate)
        next_id = create(old, next_name, directory, manifest)
        if inspect(SERVICE)["Id"] != old["Id"]:
            raise RuntimeError("another session changed the service")
        docker("stop", "-t", "15", old["Id"])
        stopped = True
        docker("rename", old["Id"], backup)
        docker("rename", next_id, SERVICE)
        docker("start", next_id)
        ready(SERVICE, 8095, directory, manifest)
        active = inspect(SERVICE)
        if active["Config"]["Env"] != old["Config"]["Env"]:
            raise RuntimeError("existing runtime environment changed")
        committed = True
        print(json.dumps({"activation": "confirmed", "backup": backup, "state_backup": str(directory / "state-before"),
                          "environment_preserved": True, "image": BASE}), flush=True)
    finally:
        if inspect(candidate):
            docker("rm", "-f", candidate)
        if not committed:
            if next_id and inspect(next_id):
                docker("rm", "-f", next_id)
            if stopped:
                current = inspect(SERVICE)
                if current is None:
                    docker("rename", old["Id"], SERVICE)
                elif current["Id"] != old["Id"]:
                    raise RuntimeError("another container owns the service; manual review required")
                docker("start", old["Id"])
                print("rollback=restored_previous", flush=True)


if __name__ == "__main__":
    try:
        main(Path(sys.argv[1]).resolve())
    except Exception as exc:
        print(f"Diff update stopped: {type(exc).__name__}; details suppressed", file=sys.stderr)
        sys.exit(1)
