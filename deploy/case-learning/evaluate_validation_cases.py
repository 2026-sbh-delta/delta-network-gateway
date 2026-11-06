"""AI-container-local export and real-Claude comparison of an explicitly held-out fixture."""
import base64
import gzip
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from delta_ai.case_evaluation import EvaluationDataset
from delta_ai.cases import CaseReview, CaseStore


def main():
    assert RUN_GROUP.isalnum() and len(RUN_GROUP) == 32
    store_path = Path(os.environ.get("DELTA_CASE_STORE", str(Path(os.environ.get("DELTA_RULE_STORE", "var/rule_candidates.json")).parent / "verified-cases.sqlite")))
    root = store_path.parent / "feedback-verification" / RUN_GROUP
    dataset = root / "held-out-candidate.json"
    result_path = root / "real-claude-evaluation.json"
    before = time.monotonic()
    def cli(args):
        result = subprocess.run([sys.executable, "-m", "delta_ai.case_cli", "--store", str(store_path), *args],
                                text=True, capture_output=True)
        assert result.returncode == 0, "Validation CLI failed"
    cli(["export", "case-" + RUN_GROUP + "-next", "--output", str(dataset)])
    value = EvaluationDataset.model_validate_json(dataset.read_bytes())
    assert len(value.candidates) == 1 and value.candidates[0].review.required_terms == ("503",)
    cli(["evaluate", str(dataset), "--llm", "claude", "--output", str(result_path)])
    result = json.loads(result_path.read_bytes())
    assert result["llm_mode"] == "claude" and result["total"] == 1 and result["with_cases_passed"] == 1
    cases = result["cases"][0]
    assert cases["baseline"]["retrieved_case_ids"] == []
    assert cases["with_cases"]["retrieved_case_ids"] == ["case-" + RUN_GROUP + "-first"]
    store = CaseStore(store_path)
    # Revoke only our isolated fixtures after verifying the real comparison; retain audit/data.
    for phase in ("first", "next"):
        identifier = "case-" + RUN_GROUP + "-" + phase
        with store.connection() as db:
            row = db.execute("SELECT revision FROM case_versions WHERE case_id=? ORDER BY sequence DESC LIMIT 1", (identifier,)).fetchone()
        store.review(identifier, row["revision"], CaseReview(decision="rejected", reviewer="authorized-validation-runner",
            reason="합성 검증 완료. 검증 사례의 검색 승인을 취소하고 원본·평가·감사를 보존한다."))
    assert store.search(value.candidates[0].case.request) == []
    return {"run_group": RUN_GROUP, "evaluation": result, "elapsed_seconds": round(time.monotonic() - before, 2),
            "fixture_approvals_revoked": True, "audit_preserved": True,
            "model_calls": 2, "limitation": "독립 기대 항목을 가진 합성1건 연결 검증이며 진단 품질 향상 전체를 측정하지 않는다."}


if __name__ == "__main__":
    try:
        print(base64.b64encode(gzip.compress(json.dumps(main(), ensure_ascii=False).encode())).decode())
    except Exception as error:
        import traceback
        raise SystemExit("Validation evaluation failed: " + type(error).__name__ + " " +
                         str([(f.name, f.lineno) for f in traceback.extract_tb(error.__traceback__)])) from None
