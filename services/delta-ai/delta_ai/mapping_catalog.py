"""AWS → GCP 대응표. 시연 대상 자원만 다루며, 여기 없는 자원은 unknown 이나 gap 으로 사람에게 넘긴다.

대응은 "가까운 것을 고른다"는 뜻이지 같은 성능이나 탐지를 보장하지 않는다.
출처: EC2 범용 타입, Compute Engine E2, GuardDuty, Security Command Center 공식 문서 (2026-11-02 확인).
GuardDuty 와 SCC 사이의 대응은 공식 대응표가 없어 팀 판단이다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InstanceSpec:
    vcpus: int
    memory_gib: float
    arch: str
    shared_core: bool = False


AWS_INSTANCES: dict[str, InstanceSpec] = {
    "t4g.nano": InstanceSpec(2, 0.5, "arm64"),
    "t4g.micro": InstanceSpec(2, 1, "arm64"),
    "t4g.small": InstanceSpec(2, 2, "arm64"),
    "t4g.medium": InstanceSpec(2, 4, "arm64"),
    "t4g.large": InstanceSpec(2, 8, "arm64"),
    "t4g.xlarge": InstanceSpec(4, 16, "arm64"),
    "t3.micro": InstanceSpec(2, 1, "x86_64"),
    "t3.small": InstanceSpec(2, 2, "x86_64"),
    "t3.medium": InstanceSpec(2, 4, "x86_64"),
    "t3.large": InstanceSpec(2, 8, "x86_64"),
    "t3.xlarge": InstanceSpec(4, 16, "x86_64"),
}
# 작은 순서. 메모리와 vCPU 가 원본 이상인 첫 타입을 고른다
GCP_INSTANCES: dict[str, InstanceSpec] = {
    "e2-micro": InstanceSpec(2, 1, "x86_64", shared_core=True),
    "e2-small": InstanceSpec(2, 2, "x86_64", shared_core=True),
    "e2-medium": InstanceSpec(2, 4, "x86_64", shared_core=True),
    "e2-standard-2": InstanceSpec(2, 8, "x86_64"),
    "e2-standard-4": InstanceSpec(4, 16, "x86_64"),
}
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
PLAN_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "EKS_PROTECTION": ("container_threats",),
    "RUNTIME_MONITORING": ("container_threats", "vm_threats"),
    "MALWARE_PROTECTION_EC2": ("vm_threats",),
}
SCC_SERVICES: dict[str, frozenset[str]] = {  # 셋 다 Security Command Center Premium 이상이다
    "Event Threat Detection": frozenset({LOG_THREATS}),
    "Container Threat Detection": frozenset({"container_threats"}),
    "VM Threat Detection": frozenset({"vm_threats"}),
}
