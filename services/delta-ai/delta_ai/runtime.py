"""런타임 보안 규칙 — 이미지 취약점, 클러스터 설정, 로그 신호를 판정한다. 스캔과 수집은 호출자가 한다."""

from __future__ import annotations

from collections.abc import Sequence

from delta_ai.contract import (
    Check,
    ClusterCheck,
    Evidence,
    Finding,
    FindingStatus,
    LogSignal,
    PackageVulnerability,
    Severity,
    VerifiedControl,
)
from delta_ai.findings import check_state, finding_id
from delta_ai.parity import CheckOutcome
from delta_ai.secrets_pattern import redact

RULES: dict[str, tuple[str, str]] = {  # rule_id → (category, 제목)
    "SCA-IMG-001": ("image", "이미지 패키지 취약점"),
    "CIS-K8S-001": ("cluster", "클러스터 보안 기준"),
    "LOG-SIG-001": ("logging", "로그 보안 신호"),
}
PACKAGE_SEVERITY: dict[str, Severity] = {"critical": "high", "high": "high", "medium": "medium", "low": "low",
                                         "unknown": "medium"}
MAX_5XX_RATE = 0.01
MAX_LISTED_IDS = 5


def _finding(rule_id: str, *, status: FindingStatus, severity: Severity, evidence: str,
             location: dict[str, str]) -> Finding:
    category, title = RULES[rule_id]
    return Finding(finding_id=finding_id(rule_id, location), rule_id=rule_id, category=category, severity=severity,
                   status=status, autofix="forbidden", title=title, location=location, evidence=redact(evidence))


def _package_finding(package: PackageVulnerability) -> Finding:
    ids = package.vulnerability_ids
    listed = ", ".join(ids[:MAX_LISTED_IDS]) + (f" 외 {len(ids) - MAX_LISTED_IDS}건" if len(ids) > MAX_LISTED_IDS else "")
    fix = f"수정 버전 {', '.join(package.fixed_versions)}" if package.fixed_versions else "수정 버전 없음"
    return _finding("SCA-IMG-001", status="fail", severity=PACKAGE_SEVERITY[package.severity],
                    location={"image": package.image, "package": package.package},
                    evidence=f"{package.package} {package.installed_version}: 취약점 {len(ids)}건, 최고 심각도 {package.severity}. {listed}. {fix}")


def _cluster_finding(check: ClusterCheck) -> Finding:
    location = {"check": check.check_id}
    if check.status == "false_positive":
        return _finding("CIS-K8S-001", status="dismissed", severity="medium", location=location,
                        evidence=f"{check.check_id} {check.title}: 오탐으로 확인 ({check.verification})")
    return _finding("CIS-K8S-001", status="fail", severity="medium", location=location,
                    evidence=f"{check.check_id} {check.title}: 실패, 오탐 여부 미확인")


def _5xx_finding(signal: LogSignal) -> Finding | None:
    location = {"source": signal.source, "signal": signal.signal}
    if not signal.total:
        return _finding("LOG-SIG-001", status="unknown", severity="medium", location=location,
                        evidence=f"{signal.source}: 5xx {signal.count}건, 집계된 요청이 없어 비율을 모른다")
    if signal.count / signal.total <= MAX_5XX_RATE:
        return None
    return _finding("LOG-SIG-001", status="fail", severity="medium", location=location,
                    evidence=f"{signal.source}: 5xx {signal.count}/{signal.total}건, 기준 {MAX_5XX_RATE:.0%} 초과")


def _signal_finding(signal: LogSignal) -> Finding | None:
    if signal.signal == "http_5xx":
        return _5xx_finding(signal)
    if signal.signal != "attack_request_allowed" or not signal.count:
        return None
    return _finding("LOG-SIG-001", status="fail", severity="medium",
                    location={"source": signal.source, "signal": signal.signal},
                    evidence=f"{signal.source}: 공격 형태 요청 {signal.count}건이 차단 없이 처리됐다. 예: {' | '.join(signal.samples)}")


def _signal_controls(signals: Sequence[LogSignal]) -> tuple[VerifiedControl, ...]:
    return tuple(VerifiedControl(rule_id="LOG-SIG-001", subject=f"{s.source} {s.signal}", observed=f"차단 {s.count}건")
                 for s in signals if s.signal in ("firewall_block", "attack_request_blocked") and s.count)


def evaluated_runtime_rules(evidence: Evidence) -> frozenset[str]:
    inputs = {"SCA-IMG-001": evidence.image_vulnerabilities, "CIS-K8S-001": evidence.cluster_checks,
              "LOG-SIG-001": evidence.log_signals}
    return frozenset(rule_id for rule_id, value in inputs.items() if value is not None)


def check_runtime(evidence: Evidence) -> CheckOutcome:
    signals = evidence.log_signals or ()
    findings = (
        *(_package_finding(p) for p in evidence.image_vulnerabilities or ()),
        *(_cluster_finding(c) for c in evidence.cluster_checks or ()),
        *(f for f in map(_signal_finding, signals) if f is not None),
    )
    return CheckOutcome(findings=findings, checks=runtime_checks(findings, evaluated_runtime_rules(evidence)),
                        verified=_signal_controls(signals))


def runtime_checks(findings: Sequence[Finding], evaluated: frozenset[str]) -> tuple[Check, ...]:
    return tuple(Check(rule_id=rule_id, name=title, state=check_state(f for f in findings if f.rule_id == rule_id))
                 for rule_id, (_, title) in RULES.items() if rule_id in evaluated)
