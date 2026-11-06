"""Fixed-host restart validation; preserve the active image/env/TLS and every state byte."""
import base64
import gzip
import hashlib
import json
import subprocess
import time
from pathlib import Path


def command(args):
    result = subprocess.run(args, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError("Restart validation command failed")
    return result.stdout


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and not p.is_symlink()}


def main():
    old = json.loads(command(["docker", "inspect", "delta-ai"]))[0]
    assert old["Config"]["Image"] == EXPECTED_IMAGE and old["State"]["Running"]
    root = Path("/opt/delta/cp-diagnostic-ai/var")
    before = hashes(root)
    assert "verified-cases.sqlite" in before
    command(["docker", "restart", "delta-ai"])
    for _ in range(40):
        check = subprocess.run(["curl", "--fail", "--silent", "--max-time", "2", "http://127.0.0.1:8090/healthz"], capture_output=True)
        if check.returncode == 0:
            break
        time.sleep(1)
    else:
        raise RuntimeError("AI did not become healthy after restart")
    new = json.loads(command(["docker", "inspect", "delta-ai"]))[0]
    assert old["Config"] == new["Config"] and before == hashes(root)
    code = """import json,os
from pathlib import Path
from delta_ai.cases import CaseStore
store=CaseStore(Path(os.environ.get('DELTA_RULE_STORE','var/rule_candidates.json')).parent/'verified-cases.sqlite')
with store.connection() as db:
 rows=db.execute('SELECT case_id,sequence,revision FROM case_versions WHERE case_id LIKE ?',('case-'+GROUP+'-%',)).fetchall()
 reviews=db.execute('SELECT payload FROM case_reviews WHERE revision IN (SELECT revision FROM case_versions WHERE case_id LIKE ?)',('case-'+GROUP+'-%',)).fetchall()
 events=db.execute('SELECT COUNT(*) FROM case_review_events WHERE revision IN (SELECT revision FROM case_versions WHERE case_id LIKE ?)',('case-'+GROUP+'-%',)).fetchone()[0]
assert len(rows)==6 and len(reviews)==2 and all(json.loads(r[0])['decision']=='rejected' for r in reviews) and events==4
print(json.dumps({'versions':len(rows),'cases':2,'reviews':'rejected','review_events':events}))
""".replace("GROUP", repr(RUN_GROUP))
    record = json.loads(command(["docker", "exec", "delta-ai", "/app/.venv/bin/python", "-c", code]))
    assert before == hashes(root)
    return {"image": EXPECTED_IMAGE, "state_files_preserved": len(before), "env_preserved": True,
            "state_hashes": before, "health": 200, "persisted_cases_after_restart": record}


if __name__ == "__main__":
    try:
        print(base64.b64encode(gzip.compress(json.dumps(main()).encode())).decode())
    except Exception as error:
        import traceback
        raise SystemExit("AI restart verification failed: " + type(error).__name__ + " " +
                         str([(f.name, f.lineno) for f in traceback.extract_tb(error.__traceback__)])) from None
