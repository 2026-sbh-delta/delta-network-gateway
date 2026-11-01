"""피드백 루프 — 긴 코드 리뷰·모의해킹 보고서를 규칙 후보로 바꾼다. 승인 전까지 검사에 쓰이지 않는다.

탐지기는 정규식 대신 "한 줄에 모두 있어야 하는 문자열" 이다. LLM 이 만든 정규식을 코드에 돌리는 위험(ReDoS)을 피하려는 임시 형식이고,
Semgrep 규칙 생성으로 바꿀 수 있다.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from delta_ai.contract import Finding, Regression, Report, ReportFinding
from delta_ai.findings import finding_id

MIN_LITERAL_CHARS = 4
MAX_LITERAL_CHARS = 80
MAX_LITERALS = 5


def candidate_id(item: ReportFinding) -> str:
    raw = "|".join((item.cwe, item.path or "", item.title))
    return f"cand-{hashlib.sha256(raw.encode()).hexdigest()[:12]}"


def report_findings(report: Report) -> tuple[Finding, ...]:
    return tuple(
        Finding(finding_id=finding_id(item.cwe, {"candidate": candidate_id(item)}), rule_id=item.cwe, category="code",
                severity=item.severity, status="fail", autofix="forbidden", title=item.title,
                location={"candidate": candidate_id(item), "path": item.path or "", "line": str(item.line or 0)},
                evidence=item.description,
                snippet=f"취약: {item.vulnerable_snippet}\n안전: {item.safe_snippet}")
        for item in report.findings
    )


def detector_error(literals: Sequence[str]) -> str | None:
    if not literals:
        return "EMPTY"
    if len(literals) > MAX_LITERALS:
        return "TOO_MANY"
    if any(len(literal) > MAX_LITERAL_CHARS for literal in literals):
        return "TOO_LONG"
    if all(len(literal) < MIN_LITERAL_CHARS for literal in literals):
        return "TOO_BROAD"
    return None


def _matches(literals: Sequence[str], text: str) -> bool:
    return any(all(literal in line for literal in literals) for line in text.splitlines())


def regression_check(literals: Sequence[str], vulnerable: str, safe: str) -> Regression:
    return Regression(vulnerable_hit=_matches(literals, vulnerable), safe_hit=_matches(literals, safe))
