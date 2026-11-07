"""Deploy only the two approved API images, preserving all other runtime config."""

import copy
import fcntl
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REGISTRY = "616723161368.dkr.ecr.ap-northeast-2.amazonaws.com"
BASES = {
    "orchestrator": (
        "delta-orchestrator",
        "delta-cp-orchestrator",
        "895705a37ccdc673514ab6be0f33bd34f29e0685625909357f76e96c768c21d6",
    ),
    "platform": (
        "delta-platform-api-1",
        "delta-platform",
        "e68ab0f08b0d4c9bd68518164d5bdbb97f1c1325c80524a79f0c993b03c91c4e",
    ),
    "platform_snapshot": (
        "delta-platform-api-1",
        "delta-platform",
        "59f7086b8867ad226ab05af28479bc1e668dc346152a824b1569c0805f47a46d",
    ),
}
ROOT = Path("/opt/delta/platform-workload-diff")


class Socket(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(20)
        self.sock.connect("/var/run/docker.sock")


def docker(*args, timeout=30):
    p = subprocess.run(
        ["docker", *args], check=False, capture_output=True, text=True, timeout=timeout
    )
    if p.returncode:
        raise RuntimeError(f"docker {args[0]} failed; details suppressed")
    return p.stdout.strip()


def inspect(name):
    p = subprocess.run(
        ["docker", "inspect", "--type", "container", name],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return json.loads(p.stdout)[0] if p.returncode == 0 else None


def create(old, name, image, mode, release, state, *, candidate=False):
    cfg, host = copy.deepcopy(old["Config"]), copy.deepcopy(old["HostConfig"])
    cfg.pop("Hostname", None)
    cfg["Image"] = image
    cfg["Labels"] = {
        **(cfg.get("Labels") or {}),
        "delta.platform-workload-diff": release,
    }
    nets = {}
    for network, values in old["NetworkSettings"]["Networks"].items():
        if network in {"host", "none"}:
            continue
        if values.get("IPAMConfig"):
            raise RuntimeError("static network identity requires review")
        nets[network] = {
            "Aliases": [name] if candidate else values.get("Aliases") or []
        }
    if candidate:
        host["RestartPolicy"] = {"Name": "no", "MaximumRetryCount": 0}
        if mode == "orchestrator":
            cmd = cfg["Cmd"]
            cmd[cmd.index("--port") + 1] = "18080"
            cmd[cmd.index("--host") + 1] = "127.0.0.1"
            host["Binds"] = [
                b for b in host["Binds"] if b.split(":")[1] != "/state"
            ] + [f"{state}:/state:rw"]
        else:
            host["PortBindings"] = {
                "8088/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18088"}]
            }
    cfg["HostConfig"] = host
    if nets:
        cfg["NetworkingConfig"] = {"EndpointsConfig": nets}
    conn = Socket("localhost", timeout=20)
    try:
        conn.request(
            "POST",
            "/containers/create?" + urllib.parse.urlencode({"name": name}),
            json.dumps(cfg),
            {"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        raw = response.read()
        if response.status != 201:
            raise RuntimeError(
                f"Docker create returned {response.status}; details suppressed"
            )
        return json.loads(raw)["Id"]
    finally:
        conn.close()


def get(port, path, data=None, headers=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, headers=headers or {}
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status, r.read(100_000)
    except urllib.error.HTTPError as err:
        return err.code, b""


def ready(mode, port, env):
    path = "/healthz" if mode == "orchestrator" else "/health/ready"
    for _ in range(30):
        try:
            if get(port, path)[0] == 200:
                break
        except OSError:
            pass
        time.sleep(1)
    else:
        raise RuntimeError("candidate health check failed")
    if mode == "orchestrator":
        assert (
            get(
                port,
                "/api/platform-diff",
                b"{}",
                {"Host": "127.0.0.1:8080", "Content-Type": "application/json"},
            )[0]
            == 422
        )
    else:
        headers = {
            "Content-Type": "application/json",
            "Origin": env["PLATFORM_PUBLIC_ORIGIN"],
            "X-Platform-Request": "1",
        }
        assert (
            get(
                port,
                "/api/v1/workflows/00000000-0000-0000-0000-000000000000/diff",
                b"{}",
                headers,
            )[0]
            == 401
        )


def main(mode, image, release):
    service, repository, digest = BASES[mode]
    base = f"{REGISTRY}/{repository}@sha256:{digest}"
    assert re.fullmatch(
        re.escape(f"{REGISTRY}/{repository}@sha256:") + r"[a-f0-9]{64}", image
    )
    assert re.fullmatch(r"[a-f0-9]{12}", release)
    ROOT.mkdir(mode=0o750, parents=True, exist_ok=True)
    lock = (ROOT / f"{service}.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    old = inspect(service)
    if not old or not old["State"]["Running"] or old["Config"]["Image"] != base:
        raise RuntimeError("current operating image changed; preserve concurrent work")
    env = dict(p.split("=", 1) for p in old["Config"]["Env"] if "=" in p)
    directory = ROOT / f"{mode}-{release}"
    directory.mkdir(mode=0o750)
    docker("image", "inspect", image)
    state = directory / "candidate-state"
    active_state = next(
        (m["Source"] for m in old["Mounts"] if m["Destination"] == "/state"), None
    )

    def idle():
        if mode == "orchestrator":
            status, body = get(8080, "/api/pipeline/runs")
            if status != 200 or json.loads(body).get("active_runs") or not active_state:
                raise RuntimeError("orchestrator has an active or unreadable run")

    def web_hash(container):
        code = "import hashlib,pathlib; p=pathlib.Path('/app/web/dist'); print(hashlib.sha256(b''.join(hashlib.sha256(x.read_bytes()).digest() for x in sorted(p.rglob('*')) if x.is_file())).hexdigest())"
        return docker("exec", container, "python", "-c", code)

    candidate, next_name, backup = (
        f"{service}-{s}-{release}"
        for s in ("candidate", "next", "before-workload-diff")
    )
    if any(inspect(x) for x in (candidate, next_name, backup)):
        raise RuntimeError("release names already exist")
    idle()
    if mode == "orchestrator":
        state.mkdir(mode=0o750)
        os.chown(state, 10001, 10001)
    old_web = web_hash(service) if mode != "orchestrator" else None
    next_id = None
    stopped = committed = False
    try:
        create(old, candidate, image, mode, release, state, candidate=True)
        docker("start", candidate)
        ready(mode, 18080 if mode == "orchestrator" else 18088, env)
        if old_web and web_hash(candidate) != old_web:
            raise RuntimeError("operating web assets would change")
        docker("rm", "-f", candidate)
        print("isolated_candidate=passed", flush=True)
        next_id = create(old, next_name, image, mode, release, state)
        if inspect(service)["Id"] != old["Id"]:
            raise RuntimeError("another session changed the service")
        idle()
        docker("stop", "-t", "12", old["Id"])
        stopped = True
        if mode == "orchestrator":
            shutil.copytree(active_state, directory / "state-before", symlinks=True)
            (directory / "state-before").chmod(0o700)
        docker("rename", old["Id"], backup)
        docker("rename", next_id, service)
        docker("start", next_id)
        ready(mode, 8080 if mode == "orchestrator" else 8088, env)
        new = inspect(service)
        assert new["Config"]["Env"] == old["Config"]["Env"]
        assert new["Config"]["Image"] == image and new["Id"] == next_id
        assert not old_web or web_hash(service) == old_web
        committed = True
        print(
            json.dumps(
                {
                    "activation": mode,
                    "image": image,
                    "backup": backup,
                    "web_assets_preserved": bool(old_web),
                    "configuration_preserved": True,
                }
            ),
            flush=True,
        )
    finally:
        if inspect(candidate):
            docker("rm", "-f", candidate)
        if not committed:
            if next_id and inspect(next_id):
                docker("rm", "-f", next_id)
            if stopped:
                if not inspect(service):
                    docker("rename", old["Id"], service)
                docker("start", old["Id"])
                print("rollback=restored_previous", flush=True)


if __name__ == "__main__":
    try:
        main(*sys.argv[1:])
    except (
        AssertionError,
        KeyError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        subprocess.SubprocessError,
    ) as error:
        print(
            f"Deployment stopped: {type(error).__name__}; details suppressed",
            file=sys.stderr,
        )
        sys.exit(1)
