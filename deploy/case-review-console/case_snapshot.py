"""Read-only metadata of immutable versions and review decisions; no raw reports returned."""

import base64
import gzip
import subprocess

code = """import json,os
from pathlib import Path
from delta_ai.cases import CaseStore
store=CaseStore(os.environ.get('DELTA_CASE_STORE',str(Path(os.environ.get('DELTA_RULE_STORE','var/rule_candidates.json')).parent/'verified-cases.sqlite')))
with store.connection() as db:
 versions=[dict(r) for r in db.execute('SELECT case_id,sequence,revision FROM case_versions ORDER BY case_id,sequence')]
 events=db.execute('SELECT COUNT(*) FROM case_review_events').fetchone()[0]
 latest=[{'case_id':r['case_id'],'revision':r['revision'],'outcome':json.loads(r['payload'])['resolution']['outcome'],'review':json.loads(r['review'])['decision'] if r['review'] else None} for r in store.list(limit=100)]
print(json.dumps({'versions':versions,'review_events':events,'latest':latest}))
"""
result = subprocess.run(
    ["docker", "exec", "delta-ai", "/app/.venv/bin/python", "-c", code],
    text=True,
    capture_output=True,
    check=False,
)
if result.returncode:
    raise SystemExit("Case snapshot failed")
print(base64.b64encode(gzip.compress(result.stdout.encode())).decode())
