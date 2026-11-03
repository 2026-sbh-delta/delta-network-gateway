"""배포 단계 규칙 — 파이프라인이 보낸 단계 결과와 지표를 판정한다. 로그의 해석은 LLM 이 돕지만 판정은 이 규칙이 정한다."""

from __future__ import annotations

from collections.abc import Sequence

from delta_ai.contract import Check, DeploymentEvidence, Finding, FindingStatus, MetricObservation
from delta_ai.findings import check_state, finding_id
from delta_ai.parity import CheckOutcome
from delta_ai.secrets_pattern import redact

RULES: dict[str, str] = {
    "DEP-STAGE-001": "배포 단계 실패",
    "DEP-METRIC-001": "지표 기준 초과",
    "DEP-METRIC-002": "지표 확인 불가",
}
MIN_METRIC_SAMPLES = 20  # 이보다 적으면 오류 1건으로 비율이 크게 흔들려 판정 근거로 쓰지 않는다
LOG_TAIL_CHARS = 4000  # 실패 원인은 대개 끝에 있다. LLM 비용과 유출 범위도 줄인다


def _finding(rule_id: str, status: FindingStatus, evidence: str, location: dict[str, str]) -> Finding:
    return Finding(finding_id=finding_id(rule_id, location), rule_id=rule_id, category="deployment", severity="high",
                   status=status, autofix="forbidden", title=RULES[rule_id], location=location, evidence=evidence)


def _stage_failure(deployment: DeploymentEvidence) -> Finding:
    code = "모름" if deployment.exit_code is None else deployment.exit_code
    # 비밀 값이 경계에서 잘려 패턴을 벗어나지 않게 자르기 전에 가린다
    tail = redact(deployment.log_tail)[-LOG_TAIL_CHARS:]
    return _finding("DEP-STAGE-001", "fail", f"{deployment.stage} 단계 실패, 종료 코드 {code}\n{tail}",
                    {"stage": deployment.stage})


def _metric_finding(metric: MetricObservation) -> Finding | None:
    location = {"metric": metric.name}
    if metric.value is None or metric.threshold is None or metric.samples is None:
        return _finding("DEP-METRIC-002", "unknown", f"{metric.name}: 값·기준·표본 수 중 모르는 값이 있다", location)
    if metric.samples < MIN_METRIC_SAMPLES:
        return _finding("DEP-METRIC-002", "unknown",
                        f"{metric.name}: 표본 {metric.samples}건은 최소 {MIN_METRIC_SAMPLES}건보다 적다", location)
    if metric.value > metric.threshold:
        return _finding("DEP-METRIC-001", "fail",
                        f"{metric.name}: {metric.value} > 기준 {metric.threshold} (표본 {metric.samples}건)", location)
    return None


def _metric_findings(deployment: DeploymentEvidence) -> tuple[Finding, ...]:
    if deployment.traffic_percent is not None and not deployment.metrics:
        return (_finding("DEP-METRIC-002", "unknown", f"트래픽 {deployment.traffic_percent}% 단계에 지표가 없다",
                         {"metric": "none"}),)
    return tuple(finding for metric in deployment.metrics if (finding := _metric_finding(metric)) is not None)


def check_deployment(deployment: DeploymentEvidence) -> CheckOutcome:
    stage = (_stage_failure(deployment),) if deployment.status == "failed" else ()
    findings = (*stage, *_metric_findings(deployment))
    return CheckOutcome(findings=findings, checks=deployment_checks(findings))


def deployment_checks(findings: Sequence[Finding]) -> tuple[Check, ...]:
    return tuple(Check(rule_id=rule_id, name=title, state=check_state(f for f in findings if f.rule_id == rule_id))
                 for rule_id, title in RULES.items())
