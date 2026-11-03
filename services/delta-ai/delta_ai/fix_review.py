"""패치 판정 — 패치를 만든 쪽의 설명이 아니라 재검증·빌드·테스트·변경 파일 측정값으로 정한다(ADR-AI-28).

pass 는 '사람이 승인해도 된다'는 뜻이다. PR 은 사람이 승인한 뒤에 만든다.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Sequence
from typing import Literal

from delta_ai.contract import Check, Finding, FindingStatus, FixReviewEvidence, VerifiedControl
from delta_ai.findings import check_state, finding_id
from delta_ai.parity import CheckOutcome
from delta_ai.secrets_pattern import redact

MAX_ATTEMPTS = 2
RULES: dict[str, str] = {
    "FIX-BEFORE-001": "수정 전 재현 기록",
    "FIX-BUILD-001": "재검증 빌드 일치",
    "FIX-DAST-001": "패치 빌드 재검증",
    "FIX-TEST-001": "패치 빌드 테스트",
    "FIX-FILES-001": "변경 파일 제한",
    "FIX-SAST-001": "패치가 만든 새 탐지",
}
# 패치가 자기 검사를 약하게 만들 수 있는 파일이다
FORBIDDEN_PATTERNS = ("test_*", "*_test.*", "conftest.py", "Jenkinsfile", ".semgrep*", "Dockerfile*")
FORBIDDEN_DIRS = ("tests", "test", ".github", ".gitlab", "pipeline", "semgrep")
Autofix = Literal["retry", "forbidden"]


def _problem(rule_id: str, status: FindingStatus, autofix: Autofix, evidence: str, *, where: str = "patch") -> Finding:
    location = {"check": where}
    return Finding(finding_id=finding_id(rule_id, location), rule_id=rule_id, category="fix_review", severity="high",
                   status=status, autofix=autofix, title=RULES[rule_id], location=location, evidence=redact(evidence))


def _is_forbidden(path: str) -> bool:
    *directories, name = path.split("/")
    return (any(part in FORBIDDEN_DIRS for part in directories)
            or any(fnmatch.fnmatchcase(name, pattern) for pattern in FORBIDDEN_PATTERNS))


def _before(evidence: FixReviewEvidence) -> list[Finding]:
    if any(r.status == "REPRODUCED" for r in evidence.before.results):
        return []
    return [_problem("FIX-BEFORE-001", "fail", "forbidden", "수정 전 점검에 재현 기록이 없어 패치가 고친 것을 알 수 없다")]


def _build(evidence: FixReviewEvidence) -> list[Finding]:
    staging, jenkins = evidence.staging_build, evidence.jenkins_build
    if staging is None or jenkins is None:
        return [_problem("FIX-BUILD-001", "unknown", "forbidden", "스테이징 또는 Jenkins 빌드 기록이 없다")]
    mismatches = []
    if evidence.before_commit == staging.commit:
        mismatches.append("수정 전과 재검증의 커밋이 같다")
    if evidence.before.run_id == evidence.after.run_id:
        mismatches.append("수정 전과 재검증의 DAST 실행이 같다")
    if staging.commit != jenkins.commit:
        mismatches.append(f"커밋 불일치: 스테이징 {staging.commit}, Jenkins {jenkins.commit}")
    if staging.image_digest != jenkins.image_digest:
        mismatches.append(f"이미지 불일치: 스테이징 {staging.image_digest}, Jenkins {jenkins.image_digest}")
    return [_problem("FIX-BUILD-001", "fail", "forbidden", "; ".join(mismatches))] if mismatches else []


def _dast(evidence: FixReviewEvidence, retry: Autofix) -> list[Finding]:
    after = {r.finding_id: r for r in evidence.after.results}
    # 수정 전에 없던 항목이 패치 빌드에서 재현돼도 막는다
    reproduced_before = (r.finding_id for r in evidence.before.results if r.status == "REPRODUCED")
    problems = []
    for item_id in dict.fromkeys((*reproduced_before, *after)):
        result = after.get(item_id)
        where = f"dast:{item_id}"
        if result is None or result.status == "INCONCLUSIVE":
            state = "결과 없음" if result is None else "INCONCLUSIVE"
            problems.append(_problem("FIX-DAST-001", "unknown", "forbidden", f"{item_id}: {state}", where=where))
        elif result.status == "REPRODUCED":
            problems.append(_problem("FIX-DAST-001", "fail", retry, f"{item_id}: 패치 빌드에서 재현", where=where))
        elif result.status != "NOT_REPRODUCED":
            problems.append(_problem("FIX-DAST-001", "fail", "forbidden", f"{item_id}: {result.status}", where=where))
    return problems


def _tests(evidence: FixReviewEvidence, retry: Autofix) -> list[Finding]:
    if evidence.tests_passed is None:
        return [_problem("FIX-TEST-001", "unknown", "forbidden", "테스트 결과가 없다")]
    return [] if evidence.tests_passed else [_problem("FIX-TEST-001", "fail", retry, "패치 빌드의 테스트가 실패했다")]


def _files(evidence: FixReviewEvidence) -> list[Finding]:
    if evidence.changed_files is None:
        return [_problem("FIX-FILES-001", "unknown", "forbidden", "변경 파일을 측정하지 않았다")]
    if not evidence.changed_files:
        return [_problem("FIX-FILES-001", "fail", "forbidden", "변경된 파일이 없다")]
    forbidden = [path for path in evidence.changed_files if _is_forbidden(path)]
    return [_problem("FIX-FILES-001", "fail", "forbidden", f"검사·CI 파일 변경: {', '.join(forbidden)}")] if forbidden else []


def _sast(evidence: FixReviewEvidence, retry: Autofix) -> list[Finding]:
    sast = evidence.sast
    if sast is None:
        return [_problem("FIX-SAST-001", "unknown", "forbidden", "기준 회차와 비교한 SAST 결과가 없다")]
    if sast.new_review:
        return [_problem("FIX-SAST-001", "unknown", "forbidden", f"판정이 불확실한 새 탐지 {sast.new_review}건")]
    return [_problem("FIX-SAST-001", "fail", retry, f"새 탐지 {sast.new_findings}건")] if sast.new_findings else []


def _verified(evidence: FixReviewEvidence, findings: Sequence[Finding]) -> tuple[VerifiedControl, ...]:
    failed = {f.rule_id for f in findings}
    subject = evidence.after.target
    observed = {"FIX-BEFORE-001": "수정 전 재현 기록 있음", "FIX-BUILD-001": "재검증 빌드와 Jenkins 기록의 커밋·digest 일치",
                "FIX-DAST-001": "재현된 항목이 패치 빌드에서 재현되지 않음", "FIX-TEST-001": "테스트 통과",
                "FIX-FILES-001": "검사·CI 파일 변경 없음", "FIX-SAST-001": "기준 회차 대비 새 탐지 없음"}
    return tuple(VerifiedControl(rule_id=rule_id, subject=subject, observed=text)
                 for rule_id, text in observed.items() if rule_id not in failed)


def check_fix_review(evidence: FixReviewEvidence) -> CheckOutcome:
    # 상한에 닿으면 패치를 다시 만들지 않는다. 사람이 판단한다
    retry: Autofix = "retry" if evidence.attempt < MAX_ATTEMPTS else "forbidden"
    findings = (*_before(evidence), *_build(evidence), *_dast(evidence, retry), *_tests(evidence, retry), *_files(evidence),
                *_sast(evidence, retry))
    return CheckOutcome(findings=findings, checks=fix_review_checks(findings), verified=_verified(evidence, findings))


def fix_review_checks(findings: Sequence[Finding]) -> tuple[Check, ...]:
    return tuple(Check(rule_id=rule_id, name=title, state=check_state(f for f in findings if f.rule_id == rule_id))
                 for rule_id, title in RULES.items())
