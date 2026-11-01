from __future__ import annotations

import copy

import pytest

from delta_ai.contract import AssessmentRequest
from delta_ai.fake_llm import FakeLLM
from delta_ai.feedback import detector_error, regression_check
from delta_ai.service import assess
from delta_ai.store import CandidateNotReady, RuleStore


async def test_feedback_loop_catches_missed_vulnerability_after_approval(feedback_request, security_request, tmp_path):
    store = RuleStore(tmp_path / "rules.json")
    llm = FakeLLM()
    code_req = AssessmentRequest.model_validate(security_request)

    before = await assess(code_req, llm=llm, store=store)
    assert "CWE-78" not in {f.rule_id for f in before.findings}

    ingest = await assess(AssessmentRequest.model_validate(feedback_request), llm=llm, store=store)
    assert ingest.verdict == "needs_human"
    assert "RULE_APPROVAL_REQUIRED" in ingest.reasons
    candidate = ingest.rule_candidates[0]
    assert candidate.status == "proposed"
    assert candidate.regression.vulnerable_hit is True
    assert candidate.regression.safe_hit is False

    unapproved = await assess(code_req, llm=llm, store=store)
    assert "CWE-78" not in {f.rule_id for f in unapproved.findings}

    store.decide(candidate.candidate_id, decision="approved", approver="yeonjae")

    after = await assess(code_req, llm=llm, store=store)
    finding = next(f for f in after.findings if f.rule_id == "CWE-78")
    assert finding.location == {"path": "server.js", "line": "14", "source": "feedback"}


async def test_reingesting_same_report_does_not_duplicate_or_reset_approval(feedback_request, tmp_path):
    store = RuleStore(tmp_path / "rules.json")
    llm = FakeLLM()
    req = AssessmentRequest.model_validate(feedback_request)
    first = await assess(req, llm=llm, store=store)
    store.decide(first.rule_candidates[0].candidate_id, decision="approved", approver="yeonjae")

    second = await assess(req, llm=llm, store=store)

    assert len(store.list_candidates()) == 1
    assert second.rule_candidates[0].status == "approved"
    assert second.verdict == "pass"


async def test_ingest_without_llm_records_candidate_that_cannot_be_approved(feedback_request, tmp_path):
    store = RuleStore(tmp_path / "rules.json")

    result = await assess(AssessmentRequest.model_validate(feedback_request), llm=None, store=store)

    candidate = result.rule_candidates[0]
    assert candidate.status == "needs_detector"
    with pytest.raises(CandidateNotReady):
        store.decide(candidate.candidate_id, decision="approved", approver="yeonjae")


async def test_candidate_failing_regression_cannot_be_approved(feedback_request, tmp_path):
    raw = copy.deepcopy(feedback_request)
    raw["evidence"]["report"]["findings"][0]["safe_snippet"] = "res.send(await runDiag(req.query.host));"
    store = RuleStore(tmp_path / "rules.json")

    result = await assess(AssessmentRequest.model_validate(raw), llm=FakeLLM(), store=store)

    candidate = result.rule_candidates[0]
    assert candidate.regression.safe_hit is True
    with pytest.raises(CandidateNotReady):
        store.decide(candidate.candidate_id, decision="approved", approver="yeonjae")


async def test_rejected_candidate_stays_inactive(feedback_request, security_request, tmp_path):
    store = RuleStore(tmp_path / "rules.json")
    llm = FakeLLM()
    ingest = await assess(AssessmentRequest.model_validate(feedback_request), llm=llm, store=store)
    store.decide(ingest.rule_candidates[0].candidate_id, decision="rejected", approver="yeonjae")

    after = await assess(AssessmentRequest.model_validate(security_request), llm=llm, store=store)

    assert "CWE-78" not in {f.rule_id for f in after.findings}


def test_store_reloads_from_disk_after_restart(tmp_path):
    from delta_ai.contract import Regression, RuleCandidate

    path = tmp_path / "rules.json"
    candidate = RuleCandidate(candidate_id="c1", rule_id="CWE-78", title="t", source="s",
                              detector_contains=("runDiag(", "req.query"), status="proposed",
                              regression=Regression(vulnerable_hit=True, safe_hit=False))
    RuleStore(path).upsert(candidate)

    assert RuleStore(path).list_candidates()[0].candidate_id == "c1"


@pytest.mark.parametrize("literals,expected", [
    (("runDiag(", "req.query"), None),
    (("a",), "TOO_BROAD"),
    (("req",), "TOO_BROAD"),
    ((), "EMPTY"),
    (("x" * 200, "req.query"), "TOO_LONG"),
    (tuple(f"literal{i}" for i in range(7)), "TOO_MANY"),
])
def test_detector_shape_limits(literals, expected):
    assert detector_error(literals) == expected


def test_regression_requires_all_literals_on_one_line():
    result = regression_check(("runDiag(", "req.query"), "runDiag(host)\nreq.query.host", "safe")

    assert result.vulnerable_hit is False
