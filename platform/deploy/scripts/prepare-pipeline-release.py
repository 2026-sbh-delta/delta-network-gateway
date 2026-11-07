"""Package the verified production UI extension without runtime credentials."""

import argparse
import gzip
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path

PYTHON_FILES = (
    "adapters/pipeline_relay.py",
    "api/pipeline.py",
    "api/production_pipeline.py",
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host-snapshot", required=True, type=Path)
    parser.add_argument("--web-dist", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    platform = Path(__file__).resolve().parents[2]
    expected = json.loads(args.host_snapshot.read_text())
    assert expected["compose_sha256"] and expected["configuration_hashes"]
    manifest = json.loads((args.web_dist / "production-ui-manifest.json").read_text())
    assert manifest["original_js_preserved_outside_edits"] is True
    files = {}
    for name, digest in manifest["baseline"].items():
        # The HTML is the only replaced baseline file; all original assets remain.
        if name != "index.html":
            assert sha((args.web_dist / name).read_bytes()) == digest
    for name, digest in manifest["files"].items():
        assert not Path(name).is_absolute() and ".." not in Path(name).parts
        data = (args.web_dist / name).read_bytes()
        assert sha(data) == digest
        files["web-patch/" + name] = data
    for name in PYTHON_FILES:
        files["python/delta_platform/" + name] = (
            platform / "backend/src/delta_platform" / name
        ).read_bytes()
    files["operate.py"] = (
        platform / "deploy/scripts/operate-pipeline-release.py"
    ).read_bytes()
    files["expected-host.json"] = (
        json.dumps(expected, sort_keys=True, indent=2) + "\n"
    ).encode()
    files["web-manifest.json"] = (
        json.dumps(manifest, sort_keys=True, indent=2) + "\n"
    ).encode()
    parent = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=platform, text=True
    ).strip()
    source = {
        "parent_commit": parent,
        "contains_local_changes": True,
        "baseline_release": manifest["baseline_release"],
        "files": {name: sha(data) for name, data in sorted(files.items())},
    }
    fingerprint = sha(json.dumps(source, sort_keys=True).encode())[:20]
    release = "pipeline-ui-" + fingerprint
    source["release"] = release
    files["source-manifest.json"] = (json.dumps(source, indent=2) + "\n").encode()
    args.output.mkdir(parents=True, exist_ok=False)
    archive = args.output / "release.tar.gz"
    with (
        archive.open("wb") as target,
        gzip.GzipFile(filename="", mode="wb", fileobj=target, mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as tar,
    ):
        for name, data in sorted(files.items()):
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode, entry.mtime = len(data), 0o600, 0
            tar.addfile(entry, io.BytesIO(data))
    result = {
        "release": release,
        "sha256": sha(archive.read_bytes()),
        "file_count": len(files),
    }
    (args.output / "release.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
