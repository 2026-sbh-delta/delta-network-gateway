"""decide() — 판정은 LLM 이 아니라 이 함수가 정한다. 보고서나 로그에 들어온 지시문이 판정을 바꿀 수 없게 하려는 것이다.

1. 막는 finding 이 없으면 pass (low 와 오탐 처리된 것은 막지 않는다)
2. 사람 확인 사유가 하나라도 있으면 needs_human
3. 전부 검증된 수정안이 있으면 fix
"""

from __future__ import annotations

from collections.abc import Sequence

from delta_ai.contract import Finding, Verdict


def _blocking(findings: Sequence[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity != "low" and f.status != "dismissed"]


def _has_remediation(finding: Finding) -> bool:
    return bool(finding.ops or finding.waf_rules or finding.code_diff)


def _reasons(blocking: Sequence[Finding]) -> list[str]:
    reasons = []
    if any(f.status == "unknown" for f in blocking):
        reasons.append("EVIDENCE_UNKNOWN")
    if any(not f.has_doc for f in blocking):
        reasons.append("NO_EVIDENCE_DOC")
    failed = [f for f in blocking if f.status == "fail"]
    if any(f.autofix == "forbidden" for f in failed):
        reasons.append("AUTOFIX_FORBIDDEN")
    if any(f.autofix == "allowed" and not _has_remediation(f) for f in failed):
        reasons.append("PATCH_MISSING")
    return reasons


def decide(findings: Sequence[Finding], extra_reasons: Sequence[str]) -> tuple[Verdict, tuple[str, ...]]:
    blocking = _blocking(findings)
    if not blocking and not extra_reasons:
        return "pass", ()
    reasons = tuple(dict.fromkeys((*extra_reasons, *_reasons(blocking))))
    if reasons:
        return "needs_human", reasons
    return "fix", ()
