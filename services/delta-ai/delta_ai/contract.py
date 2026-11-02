"""AI 판단 서비스 호출 계약. 플랫폼은 이 스키마만 알면 되고, 게이트웨이(ANF·HAProxy)나 모델 구현은 몰라도 된다.

값이 None 이면 "모른다"이다. 모르는 값을 통과로 보지 않는다 — 규칙이 unknown 으로 표시하고 사람에게 넘긴다.
"""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Kind = Literal["migration_parity", "feedback_ingest", "deployment_gate"]
Provider = Literal["aws", "gcp", "onprem"]
Verdict = Literal["pass", "fix", "needs_human"]
Severity = Literal["high", "medium", "low"]
FindingStatus = Literal["fail", "unknown", "dismissed"]
CheckState = Literal["green", "red", "unknown"]
CandidateStatus = Literal["proposed", "needs_detector", "approved", "rejected"]

MAX_LOG_TAIL_CHARS = 100_000


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ServiceRef(_Model):
    service_id: str = Field(min_length=1)
    repo: str | None = None
    commit: str | None = None


class WafInventory(_Model):
    enabled: bool | None = None
    product: str | None = None
    mode: Literal["block", "count"] | None = None
    rule_groups: tuple[str, ...] | None = None


class IdentityInventory(_Model):
    mechanism: Literal["irsa", "pod_identity", "workload_identity", "static_key", "none"] | None = None
    cloud_apis: tuple[str, ...] = ()  # 앱이 직접 호출하는 CSP API (s3 등). 출발 환경에 적는다
    cross_cloud_access: bool | None = None  # 대상 환경에서 출발 CSP API 접근이 확인됐는가


class SecretsInventory(_Model):
    source: Literal["aws_secrets_manager", "gcp_secret_manager", "k8s_secret", "vault", "env_plaintext"] | None = None
    plaintext_env: tuple[str, ...] = ()  # 평문으로 넣은 환경 변수 이름. 값은 보내지 않는다


class LoggingInventory(_Model):
    central: bool | None = None
    destination: str | None = None


class IngressInventory(_Model):
    tls: bool | None = None


class NetworkInventory(_Model):
    gateway: Literal["anf", "haproxy", "none"] | None = None
    inspected: bool | None = None  # 트래픽이 중앙 검사 경로를 지나는가
    l7_visible: bool | None = None  # 그 경로에서 복호화된 L7 을 볼 수 있는가


class EnvInventory(_Model):
    provider: Provider
    waf: WafInventory = WafInventory()
    identity: IdentityInventory = IdentityInventory()
    secrets: SecretsInventory = SecretsInventory()
    logging: LoggingInventory = LoggingInventory()
    ingress: IngressInventory = IngressInventory()
    network: NetworkInventory = NetworkInventory()


class AttackResult(_Model):
    scenario: str
    environment: Provider
    observed: Literal["blocked", "allowed", "alert_only", "unknown"]  # alert_only 는 차단이 아니다
    http_status: int | None = None


class ReportFinding(_Model):
    title: str
    cwe: str = Field(pattern=r"^CWE-\d+$")
    severity: Severity = "high"
    path: str | None = None
    line: int | None = None
    description: str = ""
    vulnerable_snippet: str = ""
    safe_snippet: str = ""


class Report(_Model):
    source: str
    findings: tuple[ReportFinding, ...]


class MetricObservation(_Model):
    """값이 threshold 를 넘으면 실패다. 값·기준·표본 수 중 하나라도 모르면 통과로 보지 않는다."""

    name: str = Field(min_length=1)
    value: float | None = None
    threshold: float | None = None
    samples: int | None = Field(default=None, ge=0)


class DeploymentEvidence(_Model):
    stage: str = Field(min_length=1)
    status: Literal["succeeded", "failed"]
    target: Provider | None = None
    traffic_percent: int | None = Field(default=None, ge=0, le=100)  # 값이 있으면 지표가 반드시 있어야 한다
    exit_code: int | None = None
    log_tail: str = Field(default="", max_length=MAX_LOG_TAIL_CHARS)
    metrics: tuple[MetricObservation, ...] = ()


class Evidence(_Model):
    attack_results: tuple[AttackResult, ...] = ()
    report: Report | None = None
    deployment: DeploymentEvidence | None = None


class AssessmentRequest(_Model):
    assessment_id: str = Field(min_length=1)
    kind: Kind
    service: ServiceRef
    source_env: EnvInventory | None = None
    target_env: EnvInventory | None = None
    evidence: Evidence = Evidence()

    @model_validator(mode="after")
    def _require_inputs_for_kind(self) -> Self:
        if self.kind == "migration_parity" and (self.source_env is None or self.target_env is None):
            raise ValueError("migration_parity 는 source_env 와 target_env 가 필요하다")
        if self.kind == "feedback_ingest" and self.evidence.report is None:
            raise ValueError("feedback_ingest 는 evidence.report 가 필요하다")
        if self.kind == "deployment_gate" and self.evidence.deployment is None:
            raise ValueError("deployment_gate 는 evidence.deployment 가 필요하다")
        return self


class SpecOp(_Model):
    """target_env 인벤토리 기준 JSON Patch 한 줄. 플랫폼이 원하는 상태로 번역한다."""

    op: Literal["replace", "add", "remove"]
    path: str
    value: Any = None


class WafRule(_Model):
    priority: int
    action: str
    expression: str
    capability: str


class Finding(_Model):
    finding_id: str
    rule_id: str
    category: str
    severity: Severity
    status: FindingStatus
    autofix: Literal["allowed", "forbidden"]
    title: str
    location: dict[str, str] = Field(default_factory=dict)
    evidence: str = ""
    snippet: str | None = None
    has_doc: bool = False
    why: str = ""
    citations: tuple[str, ...] = ()
    ops: tuple[SpecOp, ...] = ()
    waf_rules: tuple[WafRule, ...] = ()


class Check(_Model):
    rule_id: str
    name: str
    state: CheckState


class Remediation(_Model):
    spec_ops: tuple[SpecOp, ...] = ()
    waf_rules: tuple[WafRule, ...] = ()


class Regression(_Model):
    vulnerable_hit: bool
    safe_hit: bool

    @property
    def ready(self) -> bool:
        return self.vulnerable_hit and not self.safe_hit


class RuleCandidate(_Model):
    candidate_id: str
    rule_id: str
    title: str
    source: str
    severity: Severity = "high"
    detector_contains: tuple[str, ...] = ()  # 한 줄 안에 모두 있으면 잡는다
    status: CandidateStatus
    regression: Regression = Regression(vulnerable_hit=False, safe_hit=False)
    approver: str | None = None


class Meta(_Model):
    llm_used: bool = False
    model: str | None = None
    prompt_version: str | None = None
    input_hash: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    duration_ms: int = 0


class AssessmentResult(_Model):
    assessment_id: str
    kind: Kind
    verdict: Verdict
    reasons: tuple[str, ...] = ()
    findings: tuple[Finding, ...] = ()
    checks: tuple[Check, ...] = ()
    remediation: Remediation = Remediation()
    rule_candidates: tuple[RuleCandidate, ...] = ()
    meta: Meta = Meta()


class CandidateDecision(_Model):
    decision: Literal["approved", "rejected"]
    approver: str = Field(min_length=1)
