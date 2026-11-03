"""DAST 규칙 — 점검 서비스가 브라우저에서 관측한 결과를 판정한다. 재현은 점검 서비스가, 배포 여부는 여기서 정한다."""

from __future__ import annotations

from collections.abc import Sequence

from delta_ai.contract import Check, DastEvidence, DastResult, Finding, FindingStatus, Severity, VerifiedControl
from delta_ai.findings import check_state, finding_id
from delta_ai.parity import CheckOutcome
from delta_ai.secrets_pattern import redact

RULES: dict[str, str] = {
    "DAST-REPRO-001": "취약점 실측 재현",
    "DAST-BLOCK-001": "CSP 로만 막힌 취약점",
    "DAST-UNKNOWN-001": "DAST 판정 불가",
}
_BY_STATUS: dict[str, tuple[str, FindingStatus, Severity]] = {
    "REPRODUCED": ("DAST-REPRO-001", "fail", "high"),
    "BLOCKED": ("DAST-BLOCK-001", "fail", "medium"),
    "INCONCLUSIVE": ("DAST-UNKNOWN-001", "unknown", "high"),
}


def _finding(evidence: DastEvidence, result: DastResult) -> Finding | None:
    if result.status not in _BY_STATUS:
        return None
    rule_id, status, severity = _BY_STATUS[result.status]
    location = {"target": evidence.target, "finding": result.finding_id}
    # 메시지는 점검 대상의 응답을 담을 수 있으니 신뢰하지 않는 데이터로 다룬다
    return Finding(finding_id=finding_id(rule_id, location), rule_id=rule_id, category="dast", severity=severity,
                   status=status, autofix="forbidden", title=RULES[rule_id], location=location,
                   evidence=redact(f"{result.title}: {result.status}. {result.message}"))


def _control(evidence: DastEvidence, result: DastResult) -> VerifiedControl | None:
    if result.status != "NOT_REPRODUCED":
        return None
    # 이번 입력과 절차에서 실행되지 않았다는 뜻이다. 대상 전체가 안전하다는 뜻이 아니다
    return VerifiedControl(rule_id="DAST-REPRO-001", subject=f"{evidence.target} {result.finding_id}",
                           observed="재방문에서 실행되지 않음")


def check_dast(evidence: DastEvidence) -> CheckOutcome:
    findings = tuple(f for r in evidence.results if (f := _finding(evidence, r)) is not None)
    verified = tuple(c for r in evidence.results if (c := _control(evidence, r)) is not None)
    return CheckOutcome(findings=findings, checks=dast_checks(findings), verified=verified)


def dast_checks(findings: Sequence[Finding]) -> tuple[Check, ...]:
    return tuple(Check(rule_id=rule_id, name=title, state=check_state(f for f in findings if f.rule_id == rule_id))
                 for rule_id, title in RULES.items())
