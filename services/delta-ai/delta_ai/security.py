"""보안 검사 입력 — SARIF 를 Finding 으로 옮기고, 승인된 피드백 규칙을 코드에 적용하고, LLM 패치를 검사한다."""

from __future__ import annotations

import difflib
import re
from collections.abc import Sequence
from typing import Any

from delta_ai.contract import AssessmentRequest, Check, Finding, RuleCandidate, Severity
from delta_ai.findings import check_state, finding_id
from delta_ai.parity import CheckOutcome
from delta_ai.secrets_pattern import has_secret_value, redact

_CWE_TAG = re.compile(r"^(CWE-\d+)\b")
_LEVEL_SEVERITY: dict[str, Severity] = {"error": "high", "warning": "medium", "note": "low"}
_DIFF_FILE = re.compile(r"^(?:---|\+\+\+) (?:[ab]/)?(\S+)", re.MULTILINE)
CONTEXT_LINES = 15
MAX_DIFF_CHARS = 4000
MAX_DIFF_CHANGED_LINES = 40
UNKNOWN_PATH = "unknown"


def _rule_table(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rules = run.get("tool", {}).get("driver", {}).get("rules", [])
    return {rule["id"]: rule for rule in rules if "id" in rule}


def _cwe(rule: dict[str, Any]) -> str | None:
    for tag in rule.get("properties", {}).get("tags", []):
        match = _CWE_TAG.match(str(tag))
        if match:
            return match.group(1)
    return None


def _code_finding(rule_id: str, *, severity: Severity, title: str, location: dict[str, str], evidence: str,
                  snippet: str | None) -> Finding:
    return Finding(finding_id=finding_id(rule_id, location), rule_id=rule_id, category="code", severity=severity,
                   status="fail", autofix="allowed", title=title, location=location, evidence=redact(evidence),
                   snippet=redact(snippet) if snippet else None)


def parse_sarif(sarif: dict[str, Any]) -> tuple[Finding, ...]:
    findings = []
    for run in sarif.get("runs", []):
        rules = _rule_table(run)
        for result in run.get("results", []):
            tool_rule = result.get("ruleId", "unknown")
            rule = rules.get(tool_rule, {})
            level = result.get("level") or rule.get("defaultConfiguration", {}).get("level", "warning")
            physical = (result.get("locations") or [{}])[0].get("physicalLocation", {})
            region = physical.get("region", {})
            location = {"path": physical.get("artifactLocation", {}).get("uri", UNKNOWN_PATH),
                        "line": str(region.get("startLine", 0)), "tool_rule": tool_rule}
            findings.append(_code_finding(_cwe(rule) or tool_rule, severity=_LEVEL_SEVERITY.get(level, "medium"),
                                          title=rule.get("shortDescription", {}).get("text") or tool_rule,
                                          location=location, evidence=result.get("message", {}).get("text", ""),
                                          snippet=region.get("snippet", {}).get("text")))
    return tuple(findings)


def _context(code_files: dict[str, str], path: str, line: int) -> str | None:
    text = code_files.get(path)
    if text is None or line < 1:
        return None
    lines = text.splitlines()
    start = max(line - CONTEXT_LINES, 1)
    return "\n".join(f"{n}: {lines[n - 1]}" for n in range(start, min(line + CONTEXT_LINES, len(lines)) + 1))


def _detector_hits(candidate: RuleCandidate, code_files: dict[str, str]) -> list[Finding]:
    findings = []
    for path, text in sorted(code_files.items()):
        for number, line in enumerate(text.splitlines(), start=1):
            if all(literal in line for literal in candidate.detector_contains):
                findings.append(_code_finding(
                    candidate.rule_id, severity=candidate.severity, title=candidate.title,
                    location={"path": path, "line": str(number), "source": "feedback"},
                    evidence=f"승인된 피드백 규칙 {candidate.candidate_id} 이 잡았다 (출처: {candidate.source})",
                    snippet=line.strip()))
    return findings


def check_security(req: AssessmentRequest, *, active_rules: Sequence[RuleCandidate]) -> CheckOutcome:
    code_files = req.evidence.code_files
    from_sarif = parse_sarif(req.evidence.sarif) if req.evidence.sarif else ()
    seen = {(f.rule_id, f.location.get("path"), f.location.get("line")) for f in from_sarif}
    from_rules = [f for rule in active_rules for f in _detector_hits(rule, code_files)
                  if (f.rule_id, f.location.get("path"), f.location.get("line")) not in seen]
    findings = tuple(
        f.model_copy(update={"snippet": redact(context)})
        if (context := _context(code_files, f.location.get("path", ""), int(f.location.get("line", 0)))) else f
        for f in (*from_sarif, *from_rules)
    )
    return CheckOutcome(findings=findings, checks=security_checks(findings))


def security_checks(findings: Sequence[Finding]) -> tuple[Check, ...]:
    return (Check(rule_id="SAST", name="정적 보안 검사", state=check_state(findings)),)


def validate_diff(diff: str, path: str) -> str | None:
    """LLM 패치 게이트. 문제가 없으면 None, 있으면 사유 코드. 적용은 플랫폼이 샌드박스에서 한다."""
    files = _DIFF_FILE.findall(diff)
    if not files or "@@" not in diff:
        return "NOT_A_DIFF"
    if any(name != path for name in files):
        return "OTHER_FILE"
    if has_secret_value(diff):
        return "SECRET_IN_DIFF"
    changed = sum(1 for line in diff.splitlines()
                  if line[:1] in ("+", "-") and not line.startswith(("+++", "---")))
    if len(diff) > MAX_DIFF_CHARS or changed > MAX_DIFF_CHANGED_LINES:
        return "TOO_LARGE"
    return None


def _parse_hunks(diff: str) -> list[tuple[list[str], list[str]]]:
    """hunk 마다 (원본 줄, 새 줄). 줄 번호는 LLM 이 자주 틀려서 읽지 않는다."""
    hunks: list[tuple[list[str], list[str]]] = []
    for line in diff.splitlines():
        if line.startswith("@@"):
            hunks.append(([], []))
        elif not hunks or line.startswith("\\"):
            continue
        elif line.startswith("-"):
            hunks[-1][0].append(line[1:])
        elif line.startswith("+"):
            hunks[-1][1].append(line[1:])
        elif line.startswith(" ") or not line:  # 빈 문맥 줄의 공백을 지우고 쓰는 경우가 많다
            hunks[-1][0].append(line[1:])
            hunks[-1][1].append(line[1:])
    return [_strip_trailing_context(old, new) for old, new in hunks]


def _strip_trailing_context(old: list[str], new: list[str]) -> tuple[list[str], list[str]]:
    while old and new and not old[-1] and not new[-1]:
        old, new = old[:-1], new[:-1]
    return old, new


def normalize_diff(diff: str, path: str, text: str) -> str | None:
    """LLM 패치를 원본에 적용해 정확한 diff 로 다시 만든다. 원본 줄이 순서대로 없으면 None.

    게이트를 통과한 패치가 git apply 에서 실패하지 않게 하려고 LLM 이 쓴 diff 를 그대로 넘기지 않는다.
    줄바꿈을 되살릴 수 없는 파일(CRLF, 끝 줄바꿈 없음)은 사람에게 넘긴다.
    """
    if "\r" in text or not text.endswith("\n"):
        return None
    lines = text.splitlines()
    result: list[str] = []
    position = 0
    hunks = _parse_hunks(diff)
    for old, new in hunks:
        size = len(old)
        found = next((i for i in range(position, len(lines) - size + 1) if lines[i:i + size] == old), None)
        if found is None or not old:
            return None
        result += [*lines[position:found], *new]
        position = found + size
    result += lines[position:]
    if not hunks or result == lines:
        return None
    body = difflib.unified_diff(lines, result, f"a/{path}", f"b/{path}", lineterm="")
    return "\n".join(body) + "\n"
