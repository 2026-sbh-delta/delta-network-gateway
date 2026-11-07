"""Restore the preserved Diff container; keep state and the fixed container."""

import json
from pathlib import Path
import sys
import time
import urllib.request

from deploy import docker, inspect


directory = Path(sys.argv[1]).resolve()
manifest = json.loads((directory / "manifest.json").read_text())
release = manifest["release"]
current, previous = inspect("delta-diff"), inspect(f"delta-diff-before-iam-lambda-{release}")
assert current and previous and previous["State"]["Running"] is False
assert current["Config"]["Labels"].get("delta.diff.iam-scope") == release
fixed = f"delta-diff-fixed-{release}"
assert inspect(fixed) is None
docker("stop", "-t", "15", current["Id"])
docker("rename", current["Id"], fixed)
docker("rename", previous["Id"], "delta-diff")
try:
    docker("start", previous["Id"])
    for _ in range(25):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8095/healthz", timeout=2) as r:
                assert json.load(r).get("service") == "delta-diff"
                break
        except (OSError, AssertionError):
            time.sleep(1)
    else:
        raise RuntimeError("previous service unhealthy")
except Exception:
    docker("stop", "-t", "10", previous["Id"])
    docker("rename", previous["Id"], f"delta-diff-before-iam-lambda-{release}")
    docker("rename", current["Id"], "delta-diff")
    docker("start", current["Id"])
    raise
print(json.dumps({"rollback": "confirmed", "fixed_container_preserved": fixed, "state_overwritten": False}))
