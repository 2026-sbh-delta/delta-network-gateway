"""AWS ↔ GCP 대응표. 시연 대상 자원만 다루며, 여기 없는 자원은 unknown 이나 gap 으로 사람에게 넘긴다.

대응은 "가까운 것을 고른다"는 뜻이지 같은 성능이나 탐지를 보장하지 않는다.
출처: EC2 범용 타입, Compute Engine E2, GuardDuty, Security Command Center 공식 문서 (2026-11-02 확인).
GuardDuty 와 SCC 사이의 대응은 공식 대응표가 없어 팀 판단이다. GCP → AWS 는 같은 표를 거꾸로 읽는다(2026-11-04).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class InstanceSpec:
    vcpus: int
    memory_gib: float
    arch: str
    shared_core: bool = False  # GCP 공유 코어 또는 AWS T 계열 버스트. 대상일 때 부하 시험을 낸다


AWS_INSTANCES: dict[str, InstanceSpec] = {
    "t4g.nano": InstanceSpec(2, 0.5, "arm64", shared_core=True),
    "t4g.micro": InstanceSpec(2, 1, "arm64", shared_core=True),
    "t4g.small": InstanceSpec(2, 2, "arm64", shared_core=True),
    "t4g.medium": InstanceSpec(2, 4, "arm64", shared_core=True),
    "t4g.large": InstanceSpec(2, 8, "arm64", shared_core=True),
    "t4g.xlarge": InstanceSpec(4, 16, "arm64", shared_core=True),
    "t3.micro": InstanceSpec(2, 1, "x86_64", shared_core=True),
    "t3.small": InstanceSpec(2, 2, "x86_64", shared_core=True),
    "t3.medium": InstanceSpec(2, 4, "x86_64", shared_core=True),
    "t3.large": InstanceSpec(2, 8, "x86_64", shared_core=True),
    "t3.xlarge": InstanceSpec(4, 16, "x86_64", shared_core=True),
}
# 작은 순서. 메모리와 vCPU 가 원본 이상인 첫 타입을 고른다
GCP_INSTANCES: dict[str, InstanceSpec] = {
    "e2-micro": InstanceSpec(2, 1, "x86_64", shared_core=True),
    "e2-small": InstanceSpec(2, 2, "x86_64", shared_core=True),
    "e2-medium": InstanceSpec(2, 4, "x86_64", shared_core=True),
    "e2-standard-2": InstanceSpec(2, 8, "x86_64"),
    "e2-standard-4": InstanceSpec(4, 16, "x86_64"),
}
# 비용 비교용 온디맨드 시간당 단가(USD). 표에 없는 리전은 다른 리전 값으로 추정하지 않는다.
# 출처: aws.amazon.com/ec2/pricing/on-demand 의 Linux 가격 데이터, cloud.google.com/products/compute/pricing/general-purpose 의 Default 열
PRICED_ON = "2026-11-02"
HOURS_PER_MONTH = 730
AWS_HOURLY_USD: dict[str, dict[str, float]] = {
    "ap-northeast-2": {
        "t4g.nano": 0.0052, "t4g.micro": 0.0104, "t4g.small": 0.0208, "t4g.medium": 0.0416, "t4g.large": 0.0832,
        "t4g.xlarge": 0.1664, "t3.micro": 0.013, "t3.small": 0.026, "t3.medium": 0.052, "t3.large": 0.104,
        "t3.xlarge": 0.208,
    },
}
GCP_HOURLY_USD: dict[str, dict[str, float]] = {
    "asia-northeast3": {
        "e2-micro": 0.010745715, "e2-small": 0.02149143, "e2-medium": 0.04298286, "e2-standard-2": 0.08596572,
        "e2-standard-4": 0.17193144,
    },
}
COST_EXCLUDES = ("디스크", "네트워크 전송·NAT·공인 IP", "OS 라이선스", "보안 도구(Security Command Center·GuardDuty 등)", "약정·Spot·무료 등급 할인")
OS_SAME = frozenset({"ubuntu", "debian", "rhel", "rocky"})
OS_APPROXIMATE: dict[str, str] = {"amazon_linux": "RHEL 계열 (RHEL 또는 Rocky Linux)"}
AWS_ACTION_ROLES: dict[str, str] = {
    "s3:GetObject": "roles/storage.objectViewer",
    "s3:PutObject": "roles/storage.objectCreator",
    "secretsmanager:GetSecretValue": "roles/secretmanager.secretAccessor",
    "logs:PutLogEvents": "roles/logging.logWriter",
    "cloudwatch:PutMetricData": "roles/monitoring.metricWriter",
}

LOG_THREATS = "log_threats"
FOUNDATION = "GuardDuty 기본 탐지"
# 키는 GuardDuty API·Terraform aws_guardduty_detector_feature 의 실제 기능 이름이다(2026-11-04 실계정 조회로 확인).
# 여기 없는 기능(S3_DATA_EVENTS, RDS_LOGIN_EVENTS, LAMBDA_NETWORK_LOGS, AI_PROTECTION 등)은 gap 이다
PLAN_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "EKS_AUDIT_LOGS": (LOG_THREATS,),  # GKE 감사 로그 탐지는 Event Threat Detection 이 맡는다
    "EKS_RUNTIME_MONITORING": ("container_threats",),
    "RUNTIME_MONITORING": ("container_threats", "vm_threats"),
    "EBS_MALWARE_PROTECTION": ("vm_threats",),
}
CAPABILITY_LABELS: dict[str, str] = {  # 표에 보일 능력 이름. 잃는 능력을 행마다 구분하려고 쓴다
    LOG_THREATS: "로그 기반 위협 탐지",
    "container_threats": "컨테이너 위협 탐지",
    "vm_threats": "VM 위협 탐지",
}
SCC_SERVICES: dict[str, frozenset[str]] = {  # 셋 다 Security Command Center Premium 이상이다
    "Event Threat Detection": frozenset({LOG_THREATS}),
    "Container Threat Detection": frozenset({"container_threats"}),
    "VM Threat Detection": frozenset({"vm_threats"}),
}

# GCP → AWS 에서 대상 후보. E2 와 같은 x86 이라 CPU 구조를 바꾸지 않는다. t4g 는 더 싸지만 이미지 재빌드가 필요해 고르지 않는다
AWS_X86_TARGETS = ("t3.micro", "t3.small", "t3.medium", "t3.large", "t3.xlarge")
OS_APPROXIMATE_TO_AWS: dict[str, str] = {"cos": "Bottlerocket 또는 Amazon Linux 2023 (컨테이너 실행용)"}
# GCP 역할은 동작 묶음이라 한 역할이 AWS 동작 여러 개가 된다. 역할 안의 나머지 권한은 옮기지 않는다
GCP_ROLE_ACTIONS: dict[str, tuple[str, ...]] = {
    "roles/storage.objectViewer": ("s3:GetObject", "s3:ListBucket"),
    "roles/storage.objectCreator": ("s3:PutObject",),
    "roles/secretmanager.secretAccessor": ("secretsmanager:GetSecretValue",),
    "roles/logging.logWriter": ("logs:CreateLogStream", "logs:PutLogEvents"),
    "roles/monitoring.metricWriter": ("cloudwatch:PutMetricData",),
}
GUARDDUTY_FOUNDATION_CAPABILITIES = frozenset({LOG_THREATS})  # 기본 탐지는 켜기만 하면 로그 기반 탐지를 한다
# SCC 능력을 덮을 GuardDuty 기능 후보. 앞에 있을수록 먼저 고른다. EKS 전용 기능은 노드가 EC2 라 고르지 않는다
GUARDDUTY_FEATURE_CANDIDATES = ("RUNTIME_MONITORING", "EBS_MALWARE_PROTECTION")


@dataclass(frozen=True)
class Route:
    """한 방향의 대응표 묶음. mapping 은 이 값만 보고 돈다."""

    source: str
    target: str
    source_instances: Mapping[str, InstanceSpec]
    target_instances: Mapping[str, InstanceSpec]  # 작은 순서
    source_prices: Mapping[str, Mapping[str, float]]
    target_prices: Mapping[str, Mapping[str, float]]
    target_memory_unit: str
    burst_test: str  # 대상이 공유 코어·버스트 타입일 때 낼 시험
    tier_target: Mapping[str, str]
    subnet_difference: str
    os_approximate: Mapping[str, str]
    identity_source: str
    identity_target: str
    grants: Mapping[str, tuple[str, ...]]  # 출발 권한 → 대상 권한

    @property
    def label(self) -> str:
        return f"{self.source.upper()} → {self.target.upper()}"


def _frozen(mapping: Mapping) -> Mapping:
    return MappingProxyType(dict(mapping))


AWS_TO_GCP = Route(
    source="aws", target="gcp", source_instances=_frozen(AWS_INSTANCES), target_instances=_frozen(GCP_INSTANCES),
    source_prices=_frozen(AWS_HOURLY_USD), target_prices=_frozen(GCP_HOURLY_USD), target_memory_unit="GB",
    burst_test="공유 코어라 vCPU 수만으로 같다고 보지 않는다. 대상에서 부하 시험으로 확인한다",
    tier_target=_frozen({
        "public": "VPC 서브넷 + 외부 IP + 기본 인터넷 경로 + 방화벽",
        "private": "VPC 서브넷 + 외부 IP 없음 + 필요 시 Cloud NAT",
    }),
    subnet_difference="AWS 서브넷은 AZ 단위이고 경로로 공개·사설을 나눈다. GCP 서브넷은 리전 단위이고 공개·사설 구분이 없다",
    os_approximate=_frozen(OS_APPROXIMATE), identity_source="인스턴스 프로파일", identity_target="서비스 계정",
    grants=_frozen({action: (role,) for action, role in AWS_ACTION_ROLES.items()}),
)
GCP_TO_AWS = Route(
    source="gcp", target="aws", source_instances=_frozen(GCP_INSTANCES),
    target_instances=_frozen({name: AWS_INSTANCES[name] for name in AWS_X86_TARGETS}),
    source_prices=_frozen(GCP_HOURLY_USD), target_prices=_frozen(AWS_HOURLY_USD), target_memory_unit="GiB",
    burst_test="T 계열은 CPU 크레딧으로 버스트한다. 지속 부하에서 느려질 수 있어 대상에서 부하 시험으로 확인한다",
    tier_target=_frozen({
        "public": "서브넷 + 인터넷 게이트웨이 경로 + 공인 IP + 보안 그룹",
        "private": "서브넷 + 공인 IP 없음 + 필요 시 NAT 게이트웨이 경로 + 보안 그룹",
    }),
    subnet_difference="GCP 서브넷은 리전 단위이고 공개·사설 구분이 없다. AWS 서브넷은 AZ 하나에 묶이고 경로표로 공개·사설을 나눈다",
    os_approximate=_frozen(OS_APPROXIMATE_TO_AWS), identity_source="서비스 계정", identity_target="인스턴스 프로파일",
    grants=_frozen(GCP_ROLE_ACTIONS),
)
ROUTES: dict[tuple[str, str], Route] = {("aws", "gcp"): AWS_TO_GCP, ("gcp", "aws"): GCP_TO_AWS}
