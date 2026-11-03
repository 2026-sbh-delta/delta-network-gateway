from __future__ import annotations

import pytest
from pydantic import ValidationError

from delta_ai.contract import AssessmentRequest
from delta_ai.fake_llm import FakeLLM
from delta_ai.fix_review import MAX_ATTEMPTS
from delta_ai.service import assess
from delta_ai.store import RuleStore

DIGEST_B = "sha256:" + "b" * 64
DIGEST_OTHER = "sha256:" + "c" * 64


def _dast(run_id: str, status: str, finding_id: str = "F-1") -> dict:
    return {"run_id": run_id, "target": "staging", "results": [{"finding_id": finding_id, "title": "XSS", "status": status}]}


def _evidence(**overrides) -> dict:
    build = {"run_id": "run-42-2", "commit": "bbb", "image_digest": DIGEST_B}
    return {"before": _dast("dast-1", "REPRODUCED"), "after": _dast("dast-2", "NOT_REPRODUCED"), "before_commit": "aaa",
            "staging_build": build, "jenkins_build": build, "tests_passed": True, "changed_files": ["app/board.py"],
            "sast": {"new_findings": 0, "new_review": 0, "still_open": 0, "resolved": 1}, **overrides}


def _request(**overrides) -> AssessmentRequest:
    return AssessmentRequest.model_validate({"assessment_id": "fix-1", "kind": "fix_review", "service": {"service_id": "board"},
                                             "evidence": {"fix_review": _evidence(**overrides)}})


async def _assess(request: AssessmentRequest, tmp_path):
    return await assess(request, llm=FakeLLM(), store=RuleStore(tmp_path / "rules.sqlite3"))


def _rules(result) -> set[str]:
    return {f.rule_id for f in result.findings}


async def test_complete_evidence_passes_and_records_what_was_checked(tmp_path):
    result = await _assess(_request(), tmp_path)

    assert result.verdict == "pass"
    assert result.findings == ()
    assert {c.rule_id for c in result.verified_controls} >= {"FIX-DAST-001", "FIX-BUILD-001", "FIX-TEST-001"}
    assert all(c.state == "green" for c in result.checks)


@pytest.mark.parametrize("overrides,rule", [
    ({"after": _dast("dast-2", "REPRODUCED")}, "FIX-DAST-001"),
    ({"tests_passed": False}, "FIX-TEST-001"),
    ({"sast": {"new_findings": 2, "new_review": 0, "still_open": 0, "resolved": 1}}, "FIX-SAST-001"),
])
async def test_problems_the_patch_can_address_go_back_to_the_fixer(tmp_path, overrides, rule):
    result = await _assess(_request(**overrides), tmp_path)

    assert result.verdict == "fix"
    assert result.reasons == ()
    assert rule in _rules(result)


@pytest.mark.parametrize("overrides,rule,reason", [
    ({"before": _dast("dast-1", "NOT_REPRODUCED")}, "FIX-BEFORE-001", "AUTOFIX_FORBIDDEN"),
    ({"before_commit": "bbb"}, "FIX-BUILD-001", "AUTOFIX_FORBIDDEN"),
    ({"after": _dast("dast-1", "NOT_REPRODUCED")}, "FIX-BUILD-001", "AUTOFIX_FORBIDDEN"),
    ({"jenkins_build": {"run_id": "run-42-2", "commit": "bbb", "image_digest": DIGEST_OTHER}}, "FIX-BUILD-001", "AUTOFIX_FORBIDDEN"),
    ({"jenkins_build": {"run_id": "run-42-2", "commit": "zzz", "image_digest": DIGEST_B}}, "FIX-BUILD-001", "AUTOFIX_FORBIDDEN"),
    ({"after": _dast("dast-2", "BLOCKED")}, "FIX-DAST-001", "AUTOFIX_FORBIDDEN"),
    ({"changed_files": ["app/board.py", "tests/test_board.py"]}, "FIX-FILES-001", "AUTOFIX_FORBIDDEN"),
    ({"changed_files": [".github/workflows/ci.yml"]}, "FIX-FILES-001", "AUTOFIX_FORBIDDEN"),
    ({"changed_files": ["deploy/jenkins-local/pipeline/Jenkinsfile"]}, "FIX-FILES-001", "AUTOFIX_FORBIDDEN"),
    ({"changed_files": ["app/.semgrep.yml"]}, "FIX-FILES-001", "AUTOFIX_FORBIDDEN"),
    ({"changed_files": []}, "FIX-FILES-001", "AUTOFIX_FORBIDDEN"),
    ({"after": _dast("dast-2", "INCONCLUSIVE")}, "FIX-DAST-001", "EVIDENCE_UNKNOWN"),
    ({"after": _dast("dast-2", "NOT_REPRODUCED", finding_id="OTHER")}, "FIX-DAST-001", "EVIDENCE_UNKNOWN"),
    ({"tests_passed": None}, "FIX-TEST-001", "EVIDENCE_UNKNOWN"),
    ({"changed_files": None}, "FIX-FILES-001", "EVIDENCE_UNKNOWN"),
    ({"sast": None}, "FIX-SAST-001", "EVIDENCE_UNKNOWN"),
    ({"sast": {"new_findings": 1, "new_review": 1, "still_open": 0, "resolved": 1}}, "FIX-SAST-001", "EVIDENCE_UNKNOWN"),
    ({"jenkins_build": None}, "FIX-BUILD-001", "EVIDENCE_UNKNOWN"),
])
async def test_problems_a_retry_cannot_settle_stop_for_a_person(tmp_path, overrides, rule, reason):
    result = await _assess(_request(**overrides), tmp_path)

    assert result.verdict == "needs_human"
    assert reason in result.reasons
    assert rule in _rules(result)


async def test_forbidden_file_is_not_retried_even_when_the_test_also_failed(tmp_path):
    result = await _assess(_request(tests_passed=False, changed_files=["tests/test_board.py"]), tmp_path)

    assert result.verdict == "needs_human"


async def test_the_last_attempt_stops_for_a_person_instead_of_retrying(tmp_path):
    allowed = await _assess(_request(tests_passed=False, attempt=MAX_ATTEMPTS - 1), tmp_path)
    exhausted = await _assess(_request(tests_passed=False, attempt=MAX_ATTEMPTS), tmp_path)

    assert allowed.verdict == "fix"
    assert exhausted.verdict == "needs_human"
    assert "AUTOFIX_FORBIDDEN" in exhausted.reasons


async def test_every_fix_review_rule_has_a_document(tmp_path):
    bad = _request(before=_dast("dast-1", "NOT_REPRODUCED"), before_commit="bbb", after=_dast("dast-2", "REPRODUCED"),
                   tests_passed=False, changed_files=["tests/a.py"],
                   sast={"new_findings": 1, "new_review": 0, "still_open": 0, "resolved": 0})

    result = await _assess(bad, tmp_path)

    assert _rules(result) == {"FIX-BEFORE-001", "FIX-BUILD-001", "FIX-DAST-001", "FIX-TEST-001", "FIX-FILES-001", "FIX-SAST-001"}
    assert all(f.has_doc for f in result.findings)
    assert "NO_EVIDENCE_DOC" not in result.reasons


@pytest.mark.parametrize("digest", ["sha256:abc", "latest", "sha256:" + "G" * 64])
def test_a_digest_that_is_not_a_full_sha256_is_rejected(digest):
    with pytest.raises(ValidationError):
        _request(staging_build={"run_id": "r", "commit": "b", "image_digest": digest})


def test_fix_review_requires_its_evidence():
    with pytest.raises(ValidationError):
        AssessmentRequest.model_validate({"assessment_id": "a", "kind": "fix_review", "service": {"service_id": "s"}})
