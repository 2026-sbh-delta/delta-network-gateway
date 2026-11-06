"""AI-container-local CLI approval of only the user-authorized synthetic validation case."""
import base64
import gzip
import json
import os
import subprocess
import sys
from pathlib import Path

from delta_ai.cases import CaseStore, IncidentCase


def main():
    assert RUN_GROUP.isalnum() and len(RUN_GROUP) == 32 and PHASE in ("first", "next")
    path = Path(os.environ.get("DELTA_CASE_STORE", str(Path(os.environ.get("DELTA_RULE_STORE", "var/rule_candidates.json")).parent / "verified-cases.sqlite")))
    store = CaseStore(path)
    case_id = "case-" + RUN_GROUP + "-" + PHASE
    with store.connection() as db:
        row = db.execute("SELECT * FROM case_versions WHERE case_id=? ORDER BY sequence DESC LIMIT 1", (case_id,)).fetchone()
    assert row and row["sequence"] == 11 and row["revision"] == EXPECTED_REVISION
    case = IncidentCase.model_validate_json(row["payload"])
    assert case.request.service_id == "feedback-validation-" + RUN_GROUP[:12]
    assert case.resolution.outcome == ("success" if PHASE == "first" else "failure")
    details = json.loads(case.resolution.checks[0].details)
    assert details["validation_fixture"] is True and details["customer_resources_changed"] is False
    assert details["before_http"] == 503 and details["after_http"] == (200 if PHASE == "first" else 503)
    root = path.parent / "feedback-verification" / RUN_GROUP
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    review = {"decision": "approved", "reviewer": "authorized-validation-runner",
        "reason": "사용자가 승인한 합성 장애 검증이다. 실제 HTTP 재검사와 해시를 검증프로그램이 확인했다. 고객 장애/사람의 운영 승인/원인 확정이 아니다.",
        "required_terms": ["503"], "forbidden_terms": ["보안 검증이 완료되었습니다"]}
    file = root / (PHASE + "-review.json")
    file.write_text(json.dumps(review, ensure_ascii=False))
    file.chmod(0o600)
    result = subprocess.run([sys.executable, "-m", "delta_ai.case_cli", "--store", str(path),
        "review", case_id, EXPECTED_REVISION, "--file", str(file)], text=True, capture_output=True)
    assert result.returncode == 0, "Local review CLI failed"
    return {"case_id": case_id, "revision": row["revision"], "review": "approved",
            "reviewer": review["reviewer"], "evidence": details, "validation_fixture": True}


if __name__ == "__main__":
    try:
        print(base64.b64encode(gzip.compress(json.dumps(main(), ensure_ascii=False).encode())).decode())
    except Exception as error:
        import traceback
        raise SystemExit("Validation review failed: " + type(error).__name__ + " " +
                         str([(f.name, f.lineno) for f in traceback.extract_tb(error.__traceback__)])) from None
