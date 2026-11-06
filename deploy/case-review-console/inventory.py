"""Read metadata/hashes only on either existing host. No source or secrets are returned."""

import base64
import gzip
import hashlib
import json
import subprocess
from pathlib import Path


def command(args):
    return subprocess.check_output(args, text=True)


def main():
    result = {}
    for name in ("delta-ai", "delta-orchestrator"):
        inspected = subprocess.run(
            ["docker", "inspect", name], text=True, capture_output=True
        )
        if inspected.returncode:
            continue
        old = json.loads(inspected.stdout)[0]
        mounted = next(
            m for m in old["Mounts"] if m["Destination"] in ("/app/var", "/state")
        )
        root = Path(mounted["Source"])
        hashes = {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*")
            if p.is_file() and not p.is_symlink()
        }
        env = dict(s.split("=", 1) for s in old["Config"]["Env"] if "=" in s)
        code = """import hashlib,json,pathlib,delta_ai
def root(m):return pathlib.Path(next(iter(m.__path__)))
roots=[root(delta_ai)]
try:
 import delta_pipeline,delta_orchestrator
 roots += [root(delta_pipeline),root(delta_orchestrator)]
except ImportError:pass
print(json.dumps({r.name+'/'+str(p.relative_to(r)):hashlib.sha256(p.read_bytes()).hexdigest() for r in roots for p in r.rglob('*') if p.is_file() and (p.suffix in ('.py','.js','.css','.html'))}))
"""
        python = "/app/.venv/bin/python" if name == "delta-ai" else "python"
        modules = json.loads(command(["docker", "exec", name, python, "-c", code]))
        runs = [json.loads(p.read_text()) for p in (root / "runs").glob("*.json")]
        result[name] = {
            "image": old["Config"]["Image"],
            "running": old["State"]["Running"],
            "container_id": old["Id"],
            "state_files": hashes,
            "modules": modules,
            "run_count": len(runs),
            "unfinished_runs": [
                r.get("run_id")
                for r in runs
                if r.get("outcome") not in ("verified", "unresolved", "stopped")
            ],
            "public_origin": env.get("ORCH_PUBLIC_ORIGIN"),
            "review_configured": bool(env.get("DELTA_CASE_REVIEW_TOKEN")),
        }
    return result


if __name__ == "__main__":
    try:
        print(base64.b64encode(gzip.compress(json.dumps(main()).encode())).decode())
    except Exception as error:
        raise SystemExit("Inventory failed: " + type(error).__name__) from None
