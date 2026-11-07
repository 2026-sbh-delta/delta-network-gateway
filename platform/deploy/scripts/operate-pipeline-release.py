"""Add the pipeline UI/API to an existing release with a tested API-only swap."""

import copy
import fcntl
import hashlib
import http.client
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookies import SimpleCookie
from pathlib import Path, PurePosixPath

NAME = "delta-platform-api-1"
OLD_FACTORY = "delta_platform.api.app:create_app"
NEW_FACTORY = "delta_platform.api.production_pipeline:create_app"
PYTHON_FILES = (
    "adapters/pipeline_relay.py",
    "api/pipeline.py",
    "api/production_pipeline.py",
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def run(*args):
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode:
        # Docker/Compose output may contain environment values. Do not echo it.
        raise RuntimeError("Command failed: " + args[0])
    return result.stdout.strip()


def inspect(target):
    return json.loads(run("docker", "inspect", target))[0]


def env_hash(item):
    return sha(json.dumps(sorted(item["Config"].get("Env", []))).encode())


def mounts(item):
    return sorted(
        (
            {k: m.get(k) for k in ("Type", "Source", "Destination", "RW")}
            for m in item["Mounts"]
        ),
        key=lambda m: m["Destination"],
    )


def static_hashes(directory):
    assert not any(p.is_symlink() for p in directory.rglob("*")), (
        "Unexpected static symlink"
    )
    return {
        str(p.relative_to(directory)): sha(p.read_bytes())
        for p in directory.rglob("*")
        if p.is_file()
    }


def write_private(path, data):
    temporary = path.with_name("." + path.name + ".new")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def write_json(path, value):
    write_private(path, (json.dumps(value, indent=2) + "\n").encode())


class DockerSocket(http.client.HTTPConnection):
    def __init__(self):
        super().__init__("localhost", timeout=40)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect("/var/run/docker.sock")


def docker_api(method, endpoint, body=None):
    connection = DockerSocket()
    try:
        payload = json.dumps(body).encode() if body is not None else None
        connection.request(
            method, endpoint, body=payload, headers={"Content-Type": "application/json"}
        )
        response = connection.getresponse()
        data = response.read()
        if response.status not in (200, 201, 204, 304):
            raise RuntimeError("Docker request failed: " + str(response.status))
        return json.loads(data) if data else None
    finally:
        connection.close()


def json_get(port, path, cookie=None):
    headers = {"Cookie": cookie} if cookie else {}
    request = urllib.request.Request(
        "http://127.0.0.1:" + str(port) + path, headers=headers
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        data = response.read(2 * 1024 * 1024 + 1)
        assert len(data) <= 2 * 1024 * 1024, "Probe response too large"
        return json.loads(data), response.headers


def probe_pipeline(port):
    summary, headers = json_get(port, "/api/v1/auth/session")
    assert summary.get("presentation_mode") is True, (
        "Expected existing presentation access"
    )
    cookie = SimpleCookie()
    for value in headers.get_all("Set-Cookie", []):
        cookie.load(value)
    assert cookie, "Presentation session cookie was not issued"
    # Secure cookies are sent manually for this loopback-only HTTP check and never stored.
    header = "; ".join(name + "=" + value.value for name, value in cookie.items())
    result, _ = json_get(port, "/api/v1/pipeline/runs", header)
    runs = result.get("runs")
    assert isinstance(runs, list), "Pipeline runs response is not a list"
    proof = {"runs_status": 200, "run_count": len(runs)}
    if runs:
        run_id = runs[0].get("run_id")
        assert isinstance(run_id, str) and re.fullmatch(r"[A-Za-z0-9]{1,128}", run_id)
        board, _ = json_get(port, "/api/v1/pipeline/runs/" + run_id, header)
        assert board.get("run_id") == run_id, "Pipeline detail identity mismatch"
        proof["detail_status"] = 200
        nodes = [item for item in board.get("nodes", []) if isinstance(item, dict)]
        available = {item.get("id") for item in nodes}
        node = next(
            (key for key in ("build", "stage", "release") if key in available),
            None,
        )
        if node:
            try:
                log, _ = json_get(
                    port, "/api/v1/pipeline/runs/" + run_id + "/log/" + node, header
                )
                assert isinstance(log.get("text"), str), "Pipeline log text is missing"
                proof["sample_log_status"] = 200
                proof["sample_log_node"] = node
                proof["sample_log_characters"] = len(log["text"])
            except urllib.error.HTTPError as error:
                # Some nodes have no Jenkins log. This is evidence, not a success claim.
                proof["sample_log_status"] = error.code
    return proof


def wait_ready(target, port):
    for _ in range(45):
        item = inspect(target)
        if item["State"]["Status"] != "running":
            raise RuntimeError("Candidate API stopped")
        try:
            data, _ = json_get(port, "/health/ready")
            if (
                data.get("status") == "ready"
                and item["State"].get("Health", {}).get("Status") == "healthy"
            ):
                return
        except (OSError, ValueError):
            pass
        time.sleep(2)
    raise RuntimeError("API readiness timed out")


class Operator:
    def __init__(self, directory):
        self.root = Path(directory).resolve()
        assert self.root.parent == Path("/opt/delta-platform/releases")
        assert re.fullmatch(r"pipeline-ui-[A-Za-z0-9_-]+", self.root.name)
        assert os.geteuid() == 0, "Run as root through SSM"
        self.expected = json.loads((self.root / "expected-host.json").read_text())
        self.manifest = json.loads((self.root / "web-manifest.json").read_text())
        self.current = Path(self.expected["current"])
        self.compose = self.current / "compose.yaml"
        self.roles = {}
        for role in ("api", "worker", "db"):
            found = [
                item
                for item in self.expected["containers"]
                if item["name"] == "/delta-platform-" + role + "-1"
            ]
            assert len(found) == 1
            self.roles[role] = found[0]
        self.backup_name = NAME + "-before-" + self.root.name
        self.failed_name = NAME + "-failed-" + self.root.name
        self.preview_name = NAME + "-preview-" + self.root.name

    def guard(self, api_id=None, compose_sha=None):
        assert Path("/opt/delta-platform/current").resolve() == self.current
        assert sha(self.compose.read_bytes()) == (
            compose_sha or self.expected["compose_sha256"]
        ), "Compose changed"
        api = inspect(NAME)
        expected = self.roles["api"]
        assert api["Id"] == (api_id or expected["id"]), "API container changed"
        assert api["Image"] == expected["image"], "API image changed"
        assert api["State"]["Status"] == "running"
        assert api["HostConfig"]["ReadonlyRootfs"] is True
        assert env_hash(api) == expected["env_sha256"], "API environment changed"
        if api_id is None:
            assert api["State"]["StartedAt"] == expected["started_at"]
            assert mounts(api) == sorted(
                expected["mounts"], key=lambda m: m["Destination"]
            )
        for role in ("worker", "db"):
            item, expected = inspect("delta-platform-" + role + "-1"), self.roles[role]
            assert item["Id"] == expected["id"], role + " container changed"
            assert item["Image"] == expected["image"], role + " image changed"
            assert item["State"]["Status"] == "running"
            assert item["State"]["StartedAt"] == expected["started_at"], (
                role + " restarted"
            )
            assert env_hash(item) == expected["env_sha256"], (
                role + " environment changed"
            )
            assert mounts(item) == sorted(
                expected["mounts"], key=lambda m: m["Destination"]
            )
        for path, digest in self.expected["configuration_hashes"].items():
            assert sha(Path(path).read_bytes()) == digest, (
                "Runtime configuration changed"
            )
        return api

    def render_compose(self, path):
        return json.loads(
            run(
                "docker",
                "compose",
                "--env-file",
                "/etc/delta-platform/deploy.env",
                "-f",
                str(path),
                "config",
                "--format",
                "json",
            )
        )

    def replacement(self, api, package):
        config, host = copy.deepcopy(api["Config"]), copy.deepcopy(api["HostConfig"])
        command = config.get("Cmd")
        assert isinstance(command, list) and command.count(OLD_FACTORY) == 1
        config["Cmd"] = [
            NEW_FACTORY if item == OLD_FACTORY else item for item in command
        ]
        volumes = []
        for mount in api["Mounts"]:
            assert mount["Type"] == "bind", "Unexpected existing API volume type"
            if mount["Destination"] != "/app/web/dist":
                volumes.append((mount["Source"], mount["Destination"], not mount["RW"]))
        volumes.append((str(self.root / "web"), "/app/web/dist", True))
        python_hashes = {}
        for relative in PYTHON_FILES:
            source = self.root / "python/delta_platform" / relative
            assert source.is_file() and not source.is_symlink()
            source.chmod(0o644)
            python_hashes[relative] = sha(source.read_bytes())
            target = str(PurePosixPath(package) / relative)
            assert not any(v[1] == target for v in volumes), (
                "Existing Python mount collision"
            )
            volumes.append((str(source), target, True))
        host["Binds"] = [
            binding
            for binding in (host.get("Binds") or [])
            if binding.split(":")[1] != "/app/web/dist"
        ]
        if host.get("Mounts"):
            host["Mounts"] = [
                mount
                for mount in host["Mounts"]
                if mount.get("Target") != "/app/web/dist"
            ]
        host["Binds"].extend(
            source + ":" + target + ":ro"
            for source, target, readonly in volumes
            if target == "/app/web/dist" or target.startswith(package + "/")
        )
        endpoints = {}
        for network, settings in api["NetworkSettings"]["Networks"].items():
            endpoints[network] = {
                key: settings[key]
                for key in ("Aliases", "Links", "DriverOpts", "IPAMConfig")
                if settings.get(key) is not None
            }
        body = dict(
            config, HostConfig=host, NetworkingConfig={"EndpointsConfig": endpoints}
        )
        return body, volumes, python_hashes

    def compose_patch(self, body, volumes):
        original = self.compose.read_text()
        match = re.search(r"(?m)^  api:\s*$", original)
        assert match, "Expected API service"
        following = re.search(
            r"(?m)^(?:  [A-Za-z0-9_-]+:|[A-Za-z0-9_-]+:)", original[match.end() :]
        )
        end = match.end() + following.start() if following else len(original)
        block = original[match.start() : end]
        for field in ("command", "volumes"):
            value = re.search(r"(?m)^    " + field + r":.*$", block)
            if value:
                after = re.search(r"(?m)^    [A-Za-z0-9_-]+:", block[value.end() :])
                stop = value.end() + after.start() if after else len(block)
                block = block[: value.start()] + block[stop:]
        block = (
            block.rstrip()
            + "\n    command: "
            + json.dumps(body["Cmd"])
            + "\n    volumes:\n"
        )
        for source, target, readonly in volumes:
            block += (
                "      - type: bind\n        source: "
                + json.dumps(source)
                + "\n        target: "
                + json.dumps(target)
                + "\n        read_only: "
                + str(readonly).lower()
                + "\n"
            )
        patched = original[: match.start()] + block + "\n" + original[end:]
        candidate = self.compose.with_name(".compose-pipeline-candidate.yaml")
        assert not candidate.exists()
        write_private(candidate, patched.encode())
        try:
            previous, future = (
                self.render_compose(self.compose),
                self.render_compose(candidate),
            )
            previous_api, future_api = (
                previous["services"]["api"],
                future["services"]["api"],
            )
            assert future_api["image"] == body["Image"], (
                "Compose image differs from the running API pin"
            )
            previous_api.pop("command", None)
            previous_api.pop("volumes", None)
            assert future_api.pop("command") == body["Cmd"]
            rendered_volumes = future_api.pop("volumes")
            assert sorted(
                (m["source"], m["target"], m.get("read_only", False))
                for m in rendered_volumes
            ) == sorted(volumes)
            assert previous == future, "Unrelated Compose configuration changed"
        finally:
            candidate.unlink()
        return patched.encode()

    def verify_files(self, stage):
        assert static_hashes(self.root / "web") == stage["web_hashes"], (
            "Staged web changed"
        )
        for relative, digest in stage["python_hashes"].items():
            assert (
                sha((self.root / "python/delta_platform" / relative).read_bytes())
                == digest
            )
        assert (
            sha((self.root / "container-after.json").read_bytes())
            == stage["container_sha256"]
        )
        assert (
            sha((self.root / "compose-after.yaml").read_bytes())
            == stage["compose_sha256"]
        )
        assert (
            sha((self.root / "compose-before.yaml").read_bytes())
            == self.expected["compose_sha256"]
        )

    def verify_networks(self, replacement, body):
        planned = body["NetworkingConfig"]["EndpointsConfig"]
        actual = replacement["NetworkSettings"]["Networks"]
        assert set(actual) == set(planned), "API network membership changed"
        for network, settings in planned.items():
            observed = actual[network]
            assert set(settings.get("Aliases") or []) <= set(
                observed.get("Aliases") or []
            ), "API network aliases changed"
            for key, value in (settings.get("IPAMConfig") or {}).items():
                assert (observed.get("IPAMConfig") or {}).get(key) == value, (
                    "API network IPAM changed"
                )
            if settings.get("DriverOpts") is not None:
                assert observed.get("DriverOpts") == settings["DriverOpts"]

    def stage(self):
        api = self.guard()
        assert not (self.root / "web-before").exists(), "Release already staged"
        write_json(self.root / "container-before.json", api)
        write_private(self.root / "compose-before.yaml", self.compose.read_bytes())
        (self.root / "web-before").mkdir()
        run(
            "docker",
            "cp",
            api["Id"] + ":/app/web/dist/.",
            str(self.root / "web-before"),
        )
        before = static_hashes(self.root / "web-before")
        for path, digest in self.manifest["baseline"].items():
            assert before.get(path) == digest, "Live web baseline changed: " + path
        shutil.copytree(self.root / "web-before", self.root / "web")
        for relative, digest in self.manifest["files"].items():
            path = PurePosixPath(relative)
            assert not path.is_absolute() and ".." not in path.parts
            source = self.root / "web-patch" / relative
            assert source.is_file() and not source.is_symlink()
            assert sha(source.read_bytes()) == digest
            target = self.root / "web" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        for path in (self.root / "web").rglob("*"):
            path.chmod(0o755 if path.is_dir() else 0o644)
        (self.root / "web").chmod(0o755)
        after = static_hashes(self.root / "web")
        assert {p for p in before if before[p] != after.get(p)} == {"index.html"}
        assert set(after) - set(before) == set(self.manifest["files"]) - {"index.html"}
        package = run(
            "docker",
            "exec",
            api["Id"],
            "python",
            "-c",
            "import pathlib,delta_platform; print(pathlib.Path(delta_platform.__file__).parent)",
        )
        assert package.startswith("/app/") and package.endswith("/delta_platform")
        assert re.fullmatch(r"/[A-Za-z0-9_./-]+", package)
        clear = run(
            "docker",
            "exec",
            api["Id"],
            "python",
            "-c",
            "from pathlib import Path; "
            + "assert not any((Path("
            + repr(package)
            + ") / p).exists() for p in "
            + repr(PYTHON_FILES)
            + "); print('new_paths_clear')",
        )
        assert clear == "new_paths_clear", (
            "A pipeline module already exists in the base API"
        )
        body, volumes, python_hashes = self.replacement(api, package)
        write_json(self.root / "container-after.json", body)
        patched = self.compose_patch(body, volumes)
        write_private(self.root / "compose-after.yaml", patched)
        preview = copy.deepcopy(body)
        preview["Hostname"] = self.preview_name
        preview["Labels"] = {
            key: value
            for key, value in preview.get("Labels", {}).items()
            if not key.startswith("com.docker.compose.")
        }
        preview["Labels"]["delta.platform.pipeline.preview"] = self.root.name
        preview["HostConfig"]["PortBindings"] = {
            "8088/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8089"}]
        }
        preview["HostConfig"]["RestartPolicy"] = {"Name": "no", "MaximumRetryCount": 0}
        for settings in preview["NetworkingConfig"]["EndpointsConfig"].values():
            settings.pop("IPAMConfig", None)
            settings.pop("Links", None)
            settings["Aliases"] = [self.preview_name]
        preview_id = None
        try:
            preview_id = docker_api(
                "POST",
                "/containers/create?name=" + urllib.parse.quote(self.preview_name),
                preview,
            )["Id"]
            docker_api("POST", "/containers/" + preview_id + "/start")
            wait_ready(preview_id, 8089)
            proof = probe_pipeline(8089)
        finally:
            try:
                candidate = inspect(preview_id or self.preview_name)
            except RuntimeError:
                candidate = None
            if candidate:
                assert (
                    candidate["Config"]["Labels"].get("delta.platform.pipeline.preview")
                    == self.root.name
                )
                docker_api(
                    "DELETE", "/containers/" + candidate["Id"] + "?force=true&v=false"
                )
        self.guard()
        state = {
            "status": "staged",
            "old_api": api["Id"],
            "image": api["Image"],
            "web_hashes": after,
            "python_hashes": python_hashes,
            "container_sha256": sha((self.root / "container-after.json").read_bytes()),
            "compose_sha256": sha(patched),
            "preserved_static_files": len(before) - 1,
            "pipeline_probe": proof,
            "worker_db_unchanged": True,
        }
        write_json(self.root / "stage.json", state)
        print(
            json.dumps(
                {
                    k: v
                    for k, v in state.items()
                    if k not in ("web_hashes", "python_hashes")
                }
            )
        )

    def restore(self, old_id, new_id, patched_sha):
        assert Path("/opt/delta-platform/current").resolve() == self.current
        assert sha(self.compose.read_bytes()) in (
            self.expected["compose_sha256"],
            patched_sha,
        )
        if new_id is None:
            try:
                occupying = inspect(NAME)
            except RuntimeError:
                occupying = None
            if occupying and occupying["Id"] != old_id:
                planned = json.loads((self.root / "container-after.json").read_text())
                planned_config = {
                    key: value
                    for key, value in planned.items()
                    if key not in ("HostConfig", "NetworkingConfig")
                }
                # A create may succeed even if its response is lost. The unique
                # release bind paths plus exact config identify our replacement.
                if not (
                    occupying["Image"] == self.roles["api"]["image"]
                    and occupying["Config"] == planned_config
                    and occupying["HostConfig"] == planned["HostConfig"]
                ):
                    raise RuntimeError(
                        "Cannot identify container occupying the API name; no foreign container was changed"
                    )
                new_id = occupying["Id"]
        if new_id:
            replacement = inspect(new_id)
            if replacement["State"]["Status"] == "running":
                run("docker", "stop", "--time", "15", new_id)
            if replacement["Name"] == "/" + NAME:
                run("docker", "rename", new_id, self.failed_name)
        original = inspect(old_id)
        if original["Name"] != "/" + NAME:
            run("docker", "rename", old_id, NAME)
        run("docker", "start", old_id)
        data = (self.root / "compose-before.yaml").read_bytes()
        assert sha(data) == self.expected["compose_sha256"]
        write_private(self.compose, data)
        wait_ready(old_id, 8088)
        restored = self.guard(api_id=old_id)
        original_config = json.loads((self.root / "container-before.json").read_text())
        assert restored["Config"] == original_config["Config"]
        assert restored["HostConfig"] == original_config["HostConfig"]

    def apply(self):
        api = self.guard()
        state = json.loads((self.root / "stage.json").read_text())
        assert state["status"] == "staged"
        self.verify_files(state)
        existing = run("docker", "ps", "-a", "--format", "{{.Names}}").splitlines()
        assert self.backup_name not in existing and self.failed_name not in existing
        body = json.loads((self.root / "container-after.json").read_text())
        new_id, attempted = None, False
        try:
            attempted = True
            run("docker", "stop", "--time", "30", api["Id"])
            run("docker", "rename", api["Id"], self.backup_name)
            new_id = docker_api("POST", "/containers/create?name=" + NAME, body)["Id"]
            docker_api("POST", "/containers/" + new_id + "/start")
            wait_ready(new_id, 8088)
            proof = probe_pipeline(8088)
            replacement = inspect(new_id)
            assert replacement["Config"] == {
                k: v
                for k, v in body.items()
                if k not in ("HostConfig", "NetworkingConfig")
            }
            assert replacement["HostConfig"] == body["HostConfig"]
            self.verify_networks(replacement, body)
            self.guard(api_id=new_id)
            write_private(self.compose, (self.root / "compose-after.yaml").read_bytes())
            self.guard(api_id=new_id, compose_sha=state["compose_sha256"])
            applied = {
                "status": "applied",
                "old_api": api["Id"],
                "new_api": new_id,
                "image": replacement["Image"],
                "environment_unchanged": True,
                "worker_db_unchanged": True,
                "compose_sha256": state["compose_sha256"],
                "pipeline_probe": proof,
                "rollback_container": self.backup_name,
            }
            write_json(self.root / "applied.json", applied)
            print(json.dumps(applied))
        except BaseException:
            if attempted:
                self.restore(api["Id"], new_id, state["compose_sha256"])
                write_json(
                    self.root / "automatic-rollback.json",
                    {"status": "restored", "old_api": api["Id"], "failed_api": new_id},
                )
                print("Previous API and Compose restored automatically", flush=True)
            raise

    def rollback(self):
        applied = json.loads((self.root / "applied.json").read_text())
        self.guard(api_id=applied["new_api"], compose_sha=applied["compose_sha256"])
        self.restore(applied["old_api"], applied["new_api"], applied["compose_sha256"])
        result = {
            "status": "restored",
            "api": applied["old_api"],
            "worker_db_unchanged": True,
        }
        write_json(self.root / "rollback.json", result)
        print(json.dumps(result))


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("stage", "apply", "rollback"):
        raise SystemExit(
            "Usage: operate-pipeline-release.py stage|apply|rollback RELEASE_DIRECTORY"
        )
    os.umask(0o077)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    with open("/run/lock/delta-platform-ui-release.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        operation = Operator(sys.argv[2])
        getattr(operation, sys.argv[1])()


if __name__ == "__main__":
    main()
