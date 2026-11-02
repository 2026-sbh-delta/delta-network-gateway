"""AI 판단 서비스 호출 계약. 플랫폼은 이 스키마만 알면 되고, 게이트웨이(ANF·HAProxy)나 모델 구현은 몰라도 된다.

값이 None 이면 "모른다"이다. 모르는 값을 통과로 보지 않는다 — 규칙이 unknown 으로 표시하고 사람에게 넘긴다.
"""

from __future__ import annotations

import ipaddress
from typing import Annotated, Any, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

Kind = Literal["migration_parity", "feedback_ingest", "deployment_gate", "runtime_security", "dast_verification",
               "migration_mapping"]
Provider = Literal["aws", "gcp", "onprem"]
Verdict = Literal["pass", "fix", "needs_human"]
Severity = Literal["high", "medium", "low"]
FindingStatus = Literal["fail", "unknown", "dismissed"]
CheckState = Literal["green", "red", "unknown"]
CandidateStatus = Literal["proposed", "needs_detector", "approved", "rejected"]

MAX_LOG_TAIL_CHARS = 100_000
MAX_RUNTIME_PACKAGES = 50  # 패키지별로 묶은 뒤의 상한. LLM 입력이 커지지 않게 한다
MAX_LOG_SAMPLES = 5
MAX_LOG_SAMPLE_CHARS = 300


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


class FirewallRule(_Model):
    """위에서 아래로 평가되는 실효 룰 한 줄. 점검할 인터페이스의 룰만 보낸다."""

    interface: str
    action: Literal["pass", "block"]
    protocol: str = "any"
    source: str = "any"
    destination: str
    port: str | None = None  # None 은 전 포트다


class NetworkInventory(_Model):
    gateway: Literal["anf", "haproxy", "none"] | None = None
    inspected: bool | None = None  # 트래픽이 중앙 검사 경로를 지나는가
    l7_visible: bool | None = None  # 그 경로에서 복호화된 L7 을 볼 수 있는가
    gateway_addresses: tuple[str, ...] = ()  # 방화벽 자신의 주소. 관리 포트 노출 판단에 쓴다
    firewall_rules: tuple[FirewallRule, ...] | None = None  # None 은 점검 안 함, 빈 값은 수집 실패로 본다


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


class ReachabilityProbe(_Model):
    """실제 연결 시험 결과. 설정이 아니라 실측이다."""

    environment: Provider
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    expected: Literal["open", "closed"]
    observed: Literal["open", "closed", "unknown"]


class PackageVulnerability(_Model):
    """이미지의 패키지 하나에 묶은 취약점. 호출자가 CVE 단위 결과를 묶어서 보낸다."""

    image: str = Field(min_length=1)
    package: str = Field(min_length=1)
    installed_version: str = ""
    severity: Literal["critical", "high", "medium", "low", "unknown"]
    fixed_versions: tuple[str, ...] = ()
    vulnerability_ids: tuple[str, ...] = ()


class ClusterCheck(_Model):
    check_id: str = Field(min_length=1)
    title: str = ""
    status: Literal["fail", "false_positive"]
    verification: str | None = None  # 오탐이라고 본 실측 근거

    @model_validator(mode="after")
    def _false_positive_needs_verification(self) -> Self:
        if self.status == "false_positive" and not self.verification:
            raise ValueError("false_positive 는 verification 이 필요하다")
        return self


class LogSignal(_Model):
    """원본 로그가 아니라 수집기가 센 신호다. samples 는 신뢰하지 않는 데이터로 다룬다."""

    source: str = Field(min_length=1)
    signal: Literal["firewall_block", "attack_request_allowed", "attack_request_blocked", "http_5xx"]
    count: int = Field(ge=0)
    total: int | None = Field(default=None, ge=0)
    samples: tuple[Annotated[str, Field(max_length=MAX_LOG_SAMPLE_CHARS)], ...] = Field(default=(),
                                                                                       max_length=MAX_LOG_SAMPLES)


class NetworkPosture(_Model):
    """격리 상태를 판정할 실측값. None 은 측정하지 못했다는 뜻이며 안전으로 읽지 않는다."""

    upstream_firewall: Literal["managed", "unmanaged"] | None = None  # 운영자가 상위 방화벽을 바꿀 수 있는가
    node_public_ip: bool | None = None
    host_firewall: Literal["default_deny", "permissive"] | None = None
    exposed_ports: tuple[int, ...] | None = None
    network_probes: tuple[ReachabilityProbe, ...] = ()  # expected=open 은 시험 도구를 확인하는 양성 대조다
    inbound_probes: tuple[ReachabilityProbe, ...] = ()  # 노드 밖 지점에서 노드로 건 시험. NodePort 는 이것으로만 드러난다
    cluster_probes: tuple[ReachabilityProbe, ...] = ()
    namespaces_without_policy: tuple[Annotated[str, Field(pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")], ...] | None = None


class DastResult(_Model):
    finding_id: str = Field(min_length=1)
    title: str = ""
    status: Literal["REPRODUCED", "NOT_REPRODUCED", "BLOCKED", "INCONCLUSIVE"]
    message: str = ""


class DastEvidence(_Model):
    """점검 서비스가 관측한 결과. 재현 여부만 받고 배포 여부는 판단 서비스가 정한다."""

    run_id: str = Field(min_length=1)
    target: str = Field(min_length=1)
    results: tuple[DastResult, ...] = Field(min_length=1)  # 결과 없는 점검을 통과로 읽지 않는다


def _valid_cidr(value: str) -> str:
    return str(ipaddress.ip_network(value))  # 잘못된 값은 계약 검증에서 거부한다


Cidr = Annotated[str, AfterValidator(_valid_cidr)]
ValueOrigin = Literal["terraform", "default", "operator"]
SubnetTier = Literal["public", "private"]


class SourceSubnet(_Model):
    name: str = Field(min_length=1)
    cidr: Cidr
    tier: SubnetTier | None = None
    zone: str | None = None
    target_cidr: Cidr | None = None  # 없으면 기본값 대역에서 고르고 출처를 default 로 표시한다
    origin: ValueOrigin = "operator"


class ExistingSubnet(_Model):
    name: str = Field(min_length=1)
    cidr: Cidr
    tier: SubnetTier | None = None


class SourceServer(_Model):
    name: str = Field(min_length=1)
    instance_type: str | None = None
    os: str | None = None  # amazon_linux, ubuntu, rhel, rocky, debian 등
    multi_arch_image: bool | None = None  # 대상 CPU 구조용 이미지를 확인했는가
    instance_profile: bool | None = None
    permissions: tuple[str, ...] | None = None  # 프로파일이 쓰는 AWS 동작. None 은 모른다는 뜻이다
    peak_memory_percent: float | None = Field(default=None, ge=0, le=100)  # 있으면 작은 후보를 제안만 한다
    origin: ValueOrigin = "operator"


class SourceSecurity(_Model):
    guardduty_enabled: bool | None = None  # Terraform 밖에서 켰을 수 있으므로 없다고 꺼진 것으로 보지 않는다
    guardduty_plans: tuple[str, ...] = ()
    other_tools: tuple[str, ...] = ()
    origin: ValueOrigin = "operator"


class MappingInput(_Model):
    """이전 전에 무엇을 만들지 정하는 입력. 지금은 AWS → GCP 만 다룬다."""

    network_mode: Literal["create", "reuse"] = "create"
    region: str | None = None
    subnets: tuple[SourceSubnet, ...] = ()
    servers: tuple[SourceServer, ...] = ()
    security: SourceSecurity | None = None
    existing_subnets: tuple[ExistingSubnet, ...] | None = None
    reserved_cidrs: tuple[Cidr, ...] = ()
    scc_tier: Literal["standard", "premium"] | None = None
    accepted_gaps: tuple[str, ...] = ()  # 사람이 잃어도 된다고 승인한 능력. 표에는 gap 으로 남는다

    @model_validator(mode="after")
    def _require_something_to_map(self) -> Self:
        if not (self.subnets or self.servers or self.security):
            raise ValueError("mapping 은 subnets, servers, security 중 하나가 필요하다")
        return self


class Evidence(_Model):
    attack_results: tuple[AttackResult, ...] = ()
    reachability: tuple[ReachabilityProbe, ...] = ()
    report: Report | None = None
    deployment: DeploymentEvidence | None = None
    dast: DastEvidence | None = None
    # None 은 점검 안 함, 빈 값은 점검했고 나온 것이 없다는 뜻이다
    image_vulnerabilities: tuple[PackageVulnerability, ...] | None = Field(default=None, max_length=MAX_RUNTIME_PACKAGES)
    cluster_checks: tuple[ClusterCheck, ...] | None = None
    log_signals: tuple[LogSignal, ...] | None = None
    network_posture: NetworkPosture | None = None
    mapping: MappingInput | None = None


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
        if self.kind == "dast_verification" and self.evidence.dast is None:
            raise ValueError("dast_verification 은 evidence.dast 가 필요하다")
        if self.kind == "migration_mapping" and self.evidence.mapping is None:
            raise ValueError("migration_mapping 은 evidence.mapping 이 필요하다")
        evidence = self.evidence
        if self.kind == "runtime_security" and all(
                value is None for value in (evidence.image_vulnerabilities, evidence.cluster_checks, evidence.log_signals,
                                           evidence.network_posture)):
            raise ValueError("runtime_security 는 image_vulnerabilities, cluster_checks, log_signals, "
                             "network_posture 중 하나가 필요하다")
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


class VerifiedControl(_Model):
    """막혔다고 실측된 통제. finding 은 문제만 담으므로 보호 근거는 따로 남긴다."""

    rule_id: str
    environment: Provider | None = None
    subject: str
    observed: str


PostureLayerName = Literal["network", "node", "cluster"]


class PostureLayer(_Model):
    layer: PostureLayerName
    state: Literal["isolated", "open", "unknown"]
    basis: str


class PostureProposal(_Model):
    """적용은 사람이 정한다. manifest 는 검토용 초안이다."""

    layer: PostureLayerName
    action: str
    manifest: str | None = None


class PostureResult(_Model):
    overall: Literal["isolated", "partial", "open", "unknown"]
    upstream_firewall: Literal["managed", "unmanaged"] | None
    layers: tuple[PostureLayer, ...]
    proposals: tuple[PostureProposal, ...] = ()


MappingGrade = Literal["exact", "approximate", "split", "gap", "unknown"]


class MappingRow(_Model):
    """매핑표 한 행. 등급은 이전 전 판단이고 실제로 같은지는 migration_parity 와 시험으로 확인한다."""

    resource: Literal["subnet", "compute", "cpu_arch", "os", "identity", "security"]
    source: str
    target: str | None
    grade: MappingGrade
    differences: tuple[str, ...] = ()
    required_tests: tuple[str, ...] = ()
    value_source: ValueOrigin
    rule_id: str


class MappingResult(_Model):
    region: str
    network_mode: Literal["create", "reuse"]
    rows: tuple[MappingRow, ...]
    assumptions: tuple[str, ...] = ()


class AssessmentResult(_Model):
    assessment_id: str
    kind: Kind
    verdict: Verdict
    reasons: tuple[str, ...] = ()
    findings: tuple[Finding, ...] = ()
    checks: tuple[Check, ...] = ()
    verified_controls: tuple[VerifiedControl, ...] = ()
    posture: PostureResult | None = None
    mapping: MappingResult | None = None
    remediation: Remediation = Remediation()
    rule_candidates: tuple[RuleCandidate, ...] = ()
    meta: Meta = Meta()


class CandidateDecision(_Model):
    decision: Literal["approved", "rejected"]
    approver: str = Field(min_length=1)
