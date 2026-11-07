"""Apply or roll back one pipeline CSS update without restarting containers."""

import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path, PurePosixPath


def sha(data):
    return hashlib.sha256(data).hexdigest()


def atomic_write(target, data, mode):
    descriptor, temporary = tempfile.mkstemp(
        prefix=".delta-pipeline-static-", dir=target.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_json(target, value):
    atomic_write(target, (json.dumps(value, indent=2) + "\n").encode(), 0o600)


def inspect(name):
    result = subprocess.run(
        ["docker", "inspect", name], capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise RuntimeError("Cannot inspect expected container")
    return json.loads(result.stdout)[0]


def mount_summary(item):
    return sorted(
        (
            {key: mount.get(key) for key in ("Type", "Source", "Destination", "RW")}
            for mount in item["Mounts"]
        ),
        key=lambda mount: mount["Destination"],
    )


def hashes(directory):
    result = {}
    for path in directory.rglob("*"):
        assert not path.is_symlink(), "Unexpected web symlink"
        if path.is_file():
            result[str(path.relative_to(directory))] = sha(path.read_bytes())
    return result


def relative_path(value):
    path = PurePosixPath(value)
    assert not path.is_absolute() and ".." not in path.parts and str(path) == value
    return path


def ready():
    for attempt in range(3):
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:8088/health/ready", timeout=5
            ) as response:
                assert (
                    response.status == 200
                    and json.load(response).get("status") == "ready"
                )
                return
        except (OSError, ValueError, AssertionError):
            if attempt == 2:
                raise RuntimeError("Platform readiness check failed") from None
            time.sleep(1)


class StaticUpdate:
    def __init__(self, directory):
        self.root = Path(directory).resolve()
        assert os.geteuid() == 0, "Run as root through SSM"
        assert self.root.parent == Path("/opt/delta-platform/releases")
        assert re.fullmatch(r"pipeline-static-[A-Za-z0-9_-]+", self.root.name)
        self.expected = json.loads((self.root / "host-before.json").read_text())
        self.manifest = json.loads((self.root / "web-manifest.json").read_text())
        assert (
            len(self.manifest["files"]) == 2 and "index.html" in self.manifest["files"]
        )
        css = next(path for path in self.manifest["files"] if path != "index.html")
        assert (
            relative_path(css).parts[0] == "assets"
            and PurePosixPath(css).suffix == ".css"
        )
        for collection in (self.manifest["baseline"], self.manifest["files"]):
            for path, digest in collection.items():
                relative_path(path)
                assert re.fullmatch(r"[0-9a-f]{64}", digest)
        assert (
            self.manifest["files"]["index.html"]
            != self.manifest["baseline"]["index.html"]
        )
        assert len(self.expected["containers"]) == 3
        assert {item["name"] for item in self.expected["containers"]} == {
            "/delta-platform-api-1",
            "/delta-platform-worker-1",
            "/delta-platform-db-1",
        }
        api = next(
            item
            for item in self.expected["containers"]
            if item["name"] == "/delta-platform-api-1"
        )
        mounts = [
            mount for mount in api["mounts"] if mount["Destination"] == "/app/web/dist"
        ]
        assert (
            len(mounts) == 1
            and mounts[0]["Type"] == "bind"
            and mounts[0]["RW"] is False
        )
        self.web = Path(mounts[0]["Source"]).resolve()
        assert self.web.is_dir() and self.web.is_relative_to(
            "/opt/delta-platform/releases"
        )
        self.index = self.web / "index.html"

    def guard(self):
        current = Path("/opt/delta-platform/current").resolve()
        assert str(current) == self.expected["current"], "Current release changed"
        assert (
            sha((current / "compose.yaml").read_bytes())
            == self.expected["compose_sha256"]
        ), "Compose changed"
        for previous in self.expected["containers"]:
            item = inspect(previous["name"])
            assert item["Id"] == previous["id"], "Container changed"
            assert item["Image"] == previous["image"], "Image changed"
            assert item["State"]["Status"] == "running"
            assert item["State"]["StartedAt"] == previous["started_at"], (
                "Container restarted"
            )
            digest = sha(json.dumps(sorted(item["Config"].get("Env", []))).encode())
            assert digest == previous["env_sha256"], "Environment changed"
            assert mount_summary(item) == sorted(
                previous["mounts"], key=lambda mount: mount["Destination"]
            ), "Mounts changed"
        for path, digest in self.expected["configuration_hashes"].items():
            assert sha(Path(path).read_bytes()) == digest, "Runtime settings changed"

    def apply(self):
        self.guard()
        ready()
        assert not (self.root / "applied.json").exists(), "Update already applied"
        assert not (self.root / "index-before.html").exists(), "Update already prepared"
        before_hashes = hashes(self.web)
        for path, digest in self.manifest["baseline"].items():
            assert before_hashes.get(path) == digest, "Static baseline changed: " + path
        patches = {}
        for path, digest in self.manifest["files"].items():
            source = self.root / "patch" / path
            assert source.is_file() and not source.is_symlink()
            assert source.resolve().is_relative_to(self.root)
            patches[path] = source.read_bytes()
            assert sha(patches[path]) == digest, "Patch hash mismatch"
        before = self.index.read_bytes()
        atomic_write(self.root / "index-before.html", before, 0o600)
        save_json(self.root / "web-before-hashes.json", before_hashes)
        for path, data in patches.items():
            if path == "index.html":
                continue
            target = self.web / path
            assert target.parent.is_dir(), "Expected existing assets directory"
            if target.exists():
                assert not target.is_symlink() and sha(target.read_bytes()) == sha(
                    data
                ), "Asset path collision"
            else:
                atomic_write(target, data, 0o644)
        self.guard()
        assert sha(self.index.read_bytes()) == self.manifest["baseline"]["index.html"]
        try:
            atomic_write(self.index, patches["index.html"], 0o644)
            ready()
            self.guard()
            after_hashes = hashes(self.web)
            assert {
                path
                for path in before_hashes
                if before_hashes[path] != after_hashes.get(path)
            } == {"index.html"}, "An existing web file changed"
            assert set(after_hashes) - set(before_hashes) == set(patches) - set(
                before_hashes
            ), "Unexpected web files appeared"
            for path, digest in self.manifest["files"].items():
                assert after_hashes.get(path) == digest
            save_json(self.root / "web-after-hashes.json", after_hashes)
            report = {
                "status": "applied",
                "files": self.manifest["files"],
                "existing_web_files_preserved": len(before_hashes) - 1,
                "containers_restarted": 0,
                "images_environment_mounts_unchanged": True,
                "readiness": "ready",
            }
            save_json(self.root / "applied.json", report)
            print(json.dumps(report))
        except BaseException:
            assert (
                sha(self.index.read_bytes()) == self.manifest["files"]["index.html"]
            ), "Concurrent UI change detected; index was not overwritten"
            atomic_write(self.index, before, 0o644)
            ready()
            self.guard()
            save_json(
                self.root / "automatic-rollback.json",
                {
                    "status": "restored",
                    "index_sha256": sha(before),
                    "containers_restarted": 0,
                },
            )
            print("Previous UI index restored automatically", flush=True)
            raise

    def rollback(self):
        self.guard()
        assert (self.root / "applied.json").exists(), "No completed update to roll back"
        assert sha(self.index.read_bytes()) == self.manifest["files"]["index.html"], (
            "A newer UI is active"
        )
        assert hashes(self.web) == json.loads(
            (self.root / "web-after-hashes.json").read_text()
        ), "Web files changed after this update"
        before = (self.root / "index-before.html").read_bytes()
        assert sha(before) == self.manifest["baseline"]["index.html"]
        atomic_write(self.index, before, 0o644)
        ready()
        self.guard()
        restored = hashes(self.web)
        for path, digest in json.loads(
            (self.root / "web-before-hashes.json").read_text()
        ).items():
            assert restored.get(path) == digest, "Original web file was not preserved"
        result = {
            "status": "restored",
            "index_sha256": sha(before),
            "containers_restarted": 0,
        }
        save_json(self.root / "rollback.json", result)
        print(json.dumps(result))


def main():
    if len(sys.argv) not in (2, 3) or (
        len(sys.argv) == 3 and sys.argv[2] != "rollback"
    ):
        raise SystemExit(
            "Usage: update-pipeline-static.py RELEASE_DIRECTORY [rollback]"
        )
    os.umask(0o077)
    with open("/run/lock/delta-platform-ui-release.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        operation = StaticUpdate(sys.argv[1])
        operation.rollback() if len(sys.argv) == 3 else operation.apply()


if __name__ == "__main__":
    main()
