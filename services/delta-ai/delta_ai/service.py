"""assess() — 두 호출(환경 차이·피드백)이 같은 순서를 따른다: 규칙 → 근거 → LLM → 코드 판정."""

from __future__ import annotations

import json
import time
from collections.abc import Sequence

from delta_ai.contract import (
    AssessmentRequest,
    AssessmentResult,
    Finding,
    Meta,
    Regression,
    Remediation,
    RuleCandidate,
    SpecOp,
    WafRule,
)
from delta_ai.feedback import candidate_id, detector_error, regression_check, report_findings
from delta_ai.judge import PROMPT_VERSION, JudgeOutcome, judge
from delta_ai.knowledge import Chunk, load_chunks
from delta_ai.llm import LlmClient
from delta_ai.parity import check_parity, parity_checks
from delta_ai.store import RuleStore
from delta_ai.verdict import decide


def _retrieve(findings: Sequence[Finding]) -> tuple[Chunk, ...]:
    rule_ids = {f.rule_id for f in findings}
    return tuple(chunk for chunk in load_chunks() if rule_ids & set(chunk.rule_ids))


def _with_doc_flags(findings: Sequence[Finding], docs: Sequence[Chunk]) -> tuple[Finding, ...]:
    documented = {rule_id for chunk in docs for rule_id in chunk.rule_ids}
    return tuple(f.model_copy(update={"has_doc": f.rule_id in documented}) for f in findings)


def _remediation(findings: Sequence[Finding]) -> Remediation:
    """막는 finding 의 수정안만 모은다. 여러 finding 이 같은 WAF 수정안을 가리키면 한 번만 넣는다."""
    active = [f for f in findings if f.status == "fail" and f.severity != "low"]
    ops: dict[str, SpecOp] = {}
    rules: dict[str, WafRule] = {}
    for finding in active:
        ops.update({op.path: op for op in finding.ops})
        rules.update({rule.expression: rule for rule in finding.waf_rules})
    return Remediation(spec_ops=tuple(ops.values()), waf_rules=tuple(rules.values()))


def _meta(outcome: JudgeOutcome, started: float) -> Meta:
    return Meta(llm_used=outcome.llm_used, model=outcome.model,
                prompt_version=PROMPT_VERSION if outcome.llm_used else None, input_hash=outcome.input_hash,
                usage=outcome.usage, duration_ms=int((time.monotonic() - started) * 1000))


async def _assess_parity(req: AssessmentRequest, llm: LlmClient | None, started: float) -> AssessmentResult:
    outcome = check_parity(req.source_env, req.target_env, req.evidence.attack_results)
    docs = _retrieve(outcome.findings)
    judged = await judge(req.kind, _with_doc_flags(outcome.findings, docs), docs, llm)
    verdict, reasons = decide(judged.findings, judged.reasons)
    return AssessmentResult(assessment_id=req.assessment_id, kind=req.kind, verdict=verdict, reasons=reasons,
                            findings=judged.findings, checks=parity_checks(judged.findings),
                            remediation=_remediation(judged.findings), meta=_meta(judged, started))


def _candidate(item, source: str, literals: tuple[str, ...]) -> RuleCandidate:
    usable = detector_error(literals) is None
    regression = (regression_check(literals, item.vulnerable_snippet, item.safe_snippet)
                  if usable else Regression(vulnerable_hit=False, safe_hit=False))
    return RuleCandidate(candidate_id=candidate_id(item), rule_id=item.cwe, title=item.title, source=source,
                         severity=item.severity, detector_contains=literals if usable else (),
                         status="proposed" if usable else "needs_detector", regression=regression)


async def _assess_feedback(req: AssessmentRequest, llm: LlmClient | None, store: RuleStore,
                           started: float) -> AssessmentResult:
    report = req.evidence.report
    findings = report_findings(report)
    existing = {c.candidate_id: c for c in store.list_candidates() if c.status in ("approved", "rejected")}
    fresh = [f for f in findings if f.location["candidate"] not in existing]
    docs = _retrieve(fresh)
    judged = await judge(req.kind, _with_doc_flags(fresh, docs), docs, llm)
    detectors = {f.location["candidate"]: judged.detectors.get(f.finding_id, ()) for f in fresh}
    candidates = []
    for item in report.findings:
        identifier = candidate_id(item)
        candidate = existing.get(identifier) or _candidate(item, report.source, tuple(detectors.get(identifier, ())))
        if identifier not in existing:
            store.upsert(candidate)
        candidates.append(candidate)
    reasons = []
    if any(c.status == "proposed" for c in candidates):
        reasons.append("RULE_APPROVAL_REQUIRED")
    if any(c.status == "needs_detector" for c in candidates):
        reasons.append("DETECTOR_MISSING")
    reasons.extend(judged.reasons)
    return AssessmentResult(assessment_id=req.assessment_id, kind=req.kind, reasons=tuple(reasons),
                            verdict="needs_human" if reasons else "pass", findings=judged.findings,
                            rule_candidates=tuple(candidates), meta=_meta(judged, started))


async def assess(req: AssessmentRequest, *, llm: LlmClient | None, store: RuleStore) -> AssessmentResult:
    started = time.monotonic()
    if req.kind == "feedback_ingest":
        return await _assess_feedback(req, llm, store, started)
    return await _assess_parity(req, llm, started)


def result_json(result: AssessmentResult) -> str:
    return json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2)
