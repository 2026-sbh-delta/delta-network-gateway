"""Verify the approved/revoked console audit survives a normal AI restart."""

import base64
import gzip
import hashlib
import json
import sqlite3
import subprocess
import time
from pathlib import Path


def command(args):
    return subprocess.check_output(args, text=True)


def hashes(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file() and not p.is_symlink()
    }


def main():
    expected_image = globals().get("EXPECTED_IMAGE")
    old = json.loads(command(["docker", "inspect", "delta-ai"]))[0]
    assert old["Config"]["Image"] == expected_image and old["State"]["Running"]
    base = Path("/opt/delta/cp-diagnostic-ai")
    before = hashes(base / "var")
    tls = hashes(base / "tls")
    command(["docker", "restart", "delta-ai"])
    for _ in range(40):
        ready = subprocess.run(
            [
                "curl",
                "--fail",
                "--silent",
                "--max-time",
                "2",
                "http://127.0.0.1:8090/healthz",
            ],
            capture_output=True,
            check=False,
        )
        if ready.returncode == 0:
            break
        time.sleep(1)
    else:
        raise RuntimeError("AI restart health failed")
    new = json.loads(command(["docker", "inspect", "delta-ai"]))[0]
    assert (
        new["Config"] == old["Config"]
        and before == hashes(base / "var")
        and tls == hashes(base / "tls")
    )
    with sqlite3.connect(
        "file:" + str(base / "var/verified-cases.sqlite") + "?mode=ro", uri=True
    ) as db:
        counts = {
            "versions": db.execute("SELECT COUNT(*) FROM case_versions").fetchone()[0],
            "review_events": db.execute(
                "SELECT COUNT(*) FROM case_review_events"
            ).fetchone()[0],
            "approved_reviews": db.execute(
                "SELECT COUNT(*) FROM case_reviews WHERE json_extract(payload,'$.decision')='approved'"
            ).fetchone()[0],
        }
    assert counts == {"versions": 10, "review_events": 6, "approved_reviews": 0}
    assert before == hashes(base / "var")
    return {
        "health": 200,
        "image": expected_image,
        "state_files_preserved": len(before),
        "tls_env_preserved": True,
        "persisted_cases": counts,
    }


if __name__ == "__main__":
    try:
        print(base64.b64encode(gzip.compress(json.dumps(main()).encode())).decode())
    except Exception as error:  # noqa: BLE001 — sanitize remote validation failures
        raise SystemExit("Restart validation failed: " + type(error).__name__) from None
