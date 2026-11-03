from __future__ import annotations

import pytest
from pydantic import ValidationError

from delta_ai.contract import AssessmentRequest
from delta_ai.fake_llm import FakeLLM
from delta_ai.service import assess
from delta_ai.store import RuleStore


def _request(*statuses: str, message: str = "관측 메시지") -> AssessmentRequest:
    results = [{"finding_id": f"F-{i}", "title": "댓글 XSS", "status": s, "message": message}
               for i, s in enumerate(statuses)]
    return AssessmentRequest.model_validate({
        "assessment_id": "dast-1", "kind": "dast_verification", "service": {"service_id": "board"},
        "evidence": {"dast": {"run_id": "run-1", "target": "local-fixture", "results": results}},
    })


async def _assess(request: AssessmentRequest, tmp_path):
    return await assess(request, llm=FakeLLM(), store=RuleStore(tmp_path / "rules.sqlite3"))


async def test_reproduced_finding_stops_for_human_with_a_documented_rule(tmp_path):
    result = await _assess(_request("REPRODUCED"), tmp_path)

    assert result.verdict == "needs_human"
    assert result.reasons == ("AUTOFIX_FORBIDDEN",)
    assert result.findings[0].rule_id == "DAST-REPRO-001"
    assert result.findings[0].has_doc


async def test_not_reproduced_passes_and_is_recorded_as_a_verified_control(tmp_path):
    result = await _assess(_request("NOT_REPRODUCED"), tmp_path)

    assert result.verdict == "pass"
    assert [c.rule_id for c in result.verified_controls] == ["DAST-REPRO-001"]


async def test_csp_block_does_not_pass_because_the_output_is_still_unencoded(tmp_path):
    result = await _assess(_request("BLOCKED"), tmp_path)

    assert result.verdict == "needs_human"
    assert result.findings[0].rule_id == "DAST-BLOCK-001"


async def test_inconclusive_is_never_read_as_safe(tmp_path):
    result = await _assess(_request("INCONCLUSIVE"), tmp_path)

    assert result.verdict == "needs_human"
    assert "EVIDENCE_UNKNOWN" in result.reasons


async def test_one_bad_result_blocks_even_when_the_others_are_clean(tmp_path):
    result = await _assess(_request("NOT_REPRODUCED", "REPRODUCED"), tmp_path)

    assert result.verdict == "needs_human"
    assert len(result.findings) == 1


async def test_secret_in_target_message_is_redacted(tmp_path):
    secret = "AKIA" + "ABCDEFGHIJKLMNOP"
    result = await _assess(_request("REPRODUCED", message=f"응답 {secret}"), tmp_path)

    assert secret not in result.findings[0].evidence


def test_run_without_results_is_rejected():
    with pytest.raises(ValidationError):
        _request()


def test_unknown_dast_status_is_rejected():
    with pytest.raises(ValidationError):
        _request("SAFE")
