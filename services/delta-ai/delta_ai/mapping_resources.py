"""LB, PostgreSQL and k3s proposals. Configuration parity never claims runtime readiness."""

import re

from delta_ai.contract import MappingInput, MappingResult
from delta_ai.mapping_contract import SourceDatabase, SourceLoadBalancer
from delta_ai.mapping_rows import Part, graded, make_row, merge
from delta_ai.parity import CheckOutcome


def _row(rule, resource, source, target, grade, origin, notes=(), tests=()):
    return graded(
        make_row(rule, resource, source, target, grade, origin, notes, tests),
        "; ".join(notes) or f"{source}: 대상에서 동작 검증이 필요하다",
    )


def _lb(lb: SourceLoadBalancer, config: MappingInput) -> Part:
    protocols = set(lb.protocols)
    http = bool(protocols) and protocols <= {"HTTP", "HTTPS"}
    transport = bool(protocols) and protocols <= {"TCP", "UDP", "TLS", "TCP_UDP"}
    destinations = {
        "gcp": "Application Load Balancer" if http else "Network Load Balancer",
        "aws": "ALB" if http else "NLB",
        "onprem": "Service + Ingress controller"
        if http
        else "Service LoadBalancer/NodePort",
    }
    notes = ["프로토콜·리스너 포트·공개 범위·TLS 종단·헬스 체크를 대상에서 검증한다"]
    grade = "approximate"
    if not (http or transport) or not lb.ports or lb.internal is None or lb.tls is None:
        grade = "unknown"
        notes.append("리스너·공개 범위 또는 TLS 설정이 불명확하다")
    if lb.kind in ("classic", "unknown"):
        grade = "unknown"
        notes.append("LB 종류의 세부 기능을 확인해야 한다")
    target = destinations[config.target_provider]
    if config.target_provider == "onprem":
        inventory = config.target_kubernetes
        candidates = inventory.load_balancers if inventory else ()
        candidates = [
            x
            for x in candidates
            if set(lb.ports) <= set(x.ports)
            and protocols <= set(x.protocols)
            and lb.internal == x.internal
            and lb.tls == x.tls
        ]
        if len(candidates) != 1:
            grade = "unknown"
            notes.append(
                "대상 YAML에서 유일한 대응 LB를 확인하지 못했다. 외부 진입 경로도 별도 확인한다"
            )
        else:
            target += f" ({candidates[0].name})"
        notes.append(
            "k3s ServiceLB·Ingress 설치와 노드 외부 접근 가능 여부는 YAML만으로 증명되지 않는다"
        )
    return _row(
        "MAP-LB-001",
        "load_balancer",
        lb.name,
        target,
        grade,
        lb.origin,
        notes,
        ("원래 Host·포트·TLS로 헬스 및 요청 전달을 검사한다",),
    )


def _major(version):
    return version.split(".")[0] if version else None


def _db(db: SourceDatabase, config: MappingInput) -> Part:
    target = {
        "aws": "RDS PostgreSQL",
        "gcp": "Cloud SQL for PostgreSQL",
        "onprem": "PostgreSQL StatefulSet + Service + PVC",
    }[config.target_provider]
    notes = [
        "확장·콜레이션·권한·TLS·연결 설정과 덤프/복원 호환성은 별도 확인한다",
        "DB 생성·복사·쓰기 전환은 실행 도구가 담당하며 이 결과는 이관 완료가 아니다",
    ]
    grade = "approximate"
    if db.engine is None:
        grade = "unknown"
        notes.append("원본 DB 엔진을 확인하지 못했다")
    elif db.engine != "postgresql":
        grade = "gap"
        notes.append(
            "일반 PostgreSQL만 대응한다. Aurora 전용 기능·다른 DB 엔진은 자동 대응하지 않는다"
        )
    elif (
        not db.version
        or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", db.version)
        or db.storage_gib is None
        or db.high_availability is None
    ):
        grade = "unknown"
        notes.append("원본 버전·스토리지 또는 HA 구성을 확인하지 못했다")
    if config.target_provider == "onprem":
        inventory = config.target_kubernetes
        matches = (
            [x for x in inventory.databases if x.name == db.name] if inventory else []
        )
        if not matches and inventory and len(inventory.databases) == 1:
            matches = list(inventory.databases)
        if len(matches) != 1:
            grade = "unknown" if grade != "gap" else grade
            notes.append("대상 YAML의 PostgreSQL과 영속 볼륨을 확인해야 한다")
        else:
            other = matches[0]
            target += (
                f" ({other.name}, version={other.version}, {other.storage_gib} GiB)"
            )
            if other.engine != "postgresql" or (
                db.version
                and other.version
                and _major(db.version) != _major(other.version)
            ):
                grade = "gap"
                notes.append(
                    "엔진 또는 PostgreSQL 주 버전이 다르다. 호환성 검증 없이 복원하지 않는다"
                )
            elif not other.version or other.storage_gib is None:
                grade = "unknown" if grade != "gap" else grade
                notes.append("대상 버전·PVC 크기가 불명확하다")
            if (
                db.storage_gib
                and other.storage_gib is not None
                and other.storage_gib < db.storage_gib
            ):
                grade = "gap"
                notes.append("대상 PVC가 원본 할당 스토리지보다 작다")
            if db.high_availability and other.high_availability is not True:
                grade = "gap"
                notes.append("관리형 HA를 단순 PostgreSQL Pod 복제로 대체할 수 없다")
    return _row(
        "MAP-DB-001",
        "database",
        f"{db.name}: {db.engine} {db.version}",
        target,
        grade,
        db.origin,
        notes,
        ("덤프/복원·행 수·새 쓰기·재시작 후 데이터 보존을 검증한다",),
    )


def map_resources(config: MappingInput) -> Part:
    return merge(
        (
            *(_lb(x, config) for x in config.load_balancers),
            *(_db(x, config) for x in config.databases),
        )
    )


def check_onprem_mapping(config: MappingInput) -> CheckOutcome:
    from delta_ai.mapping import _load_defaults, mapping_checks

    parts = [map_resources(config)]
    if config.target_provider == "onprem":
        for server in config.servers:
            parts.append(
                _row(
                    "MAP-K8S-001",
                    "compute",
                    server.name,
                    "기존 k3s의 Deployment/Pod",
                    "unknown",
                    server.origin,
                    (
                        "VM 사양을 Pod requests/limits로 동일시하지 않는다. 이미지·노드 용량·권한을 확인한다",
                    ),
                )
            )
        for subnet in config.subnets:
            parts.append(
                _row(
                    "MAP-NET-001",
                    "subnet",
                    subnet.name,
                    "기존 k3s CNI + NetworkPolicy",
                    "unknown",
                    subnet.origin,
                    (
                        "VPC 서브넷과 Pod 네트워크는 동일하지 않다. CNI·대역·실제 통신을 확인한다",
                    ),
                )
            )
        if config.security:
            parts.append(
                _row(
                    "MAP-SEC-001",
                    "security",
                    "출발 보안 도구",
                    None,
                    "gap",
                    config.security.origin,
                    ("GuardDuty/SCC의 대응이 k3s에 자동으로 생기지 않는다",),
                )
            )
    inventory = (
        config.kubernetes
        if config.source_provider == "onprem"
        else config.target_kubernetes
    )
    if inventory:
        for work in inventory.workloads:
            notes = [
                f"{work.kind}, replicas={work.replicas}, CPU={work.cpu_requests}, memory={work.memory_requests}",
                "이미지 플랫폼·노드 용량·스케줄링·상태 저장 조건은 실제 환경에서 검증한다",
            ]
            parts.append(
                _row(
                    "MAP-K8S-001",
                    "workload",
                    f"{work.namespace}/{work.name}",
                    "컨테이너 실행 환경"
                    if config.target_provider != "onprem"
                    else "기존 k3s workload",
                    "unknown"
                    if not work.images
                    or not work.cpu_requests
                    or not work.memory_requests
                    else "approximate",
                    "kubernetes",
                    notes,
                    ("동일 이미지로 헬스·Pod 재시작·권한을 검사한다",),
                )
            )
        for volume in inventory.volumes:
            parts.append(
                _row(
                    "MAP-K8S-002",
                    "storage",
                    f"{volume.namespace}/{volume.name}",
                    f"영속 스토리지 {volume.size_gib} GiB, class={volume.storage_class}",
                    "unknown",
                    "kubernetes",
                    (
                        "PVC 선언은 백업·복원·실제 볼륨 가용성의 증거가 아니다. 접근 모드와 스토리지 클래스를 확인한다",
                    ),
                )
            )
        for name in inventory.network_policies:
            parts.append(
                _row(
                    "MAP-K8S-003",
                    "security",
                    name,
                    "NetworkPolicy/대상 방화벽",
                    "unknown",
                    "kubernetes",
                    (
                        "정책 YAML의 존재만으로 허용·차단 동작을 보장하지 않는다. 선택자·CNI·통신을 검증한다",
                    ),
                )
            )
        for kind in inventory.unsupported_kinds:
            parts.append(
                _row(
                    "MAP-K8S-001",
                    "workload",
                    kind,
                    None,
                    "gap",
                    "kubernetes",
                    ("지원하지 않는 Kubernetes kind는 자동 변환하지 않는다",),
                )
            )
    else:
        parts.append(
            _row(
                "MAP-K8S-001",
                "workload",
                "k3s",
                None,
                "unknown",
                "operator",
                ("대상 k3s YAML이 없다",),
            )
        )
    result = merge(parts)
    mapping = MappingResult(
        region=config.region
        or (
            "onprem"
            if config.target_provider == "onprem"
            else _load_defaults()["regions"][config.target_provider]
        ),
        network_mode="reuse"
        if config.target_provider == "onprem"
        else config.network_mode,
        rows=result.rows,
        assumptions=("YAML은 선언된 구성이다. 실제 클러스터 관측·배포 완료가 아니다",),
    )
    return CheckOutcome(
        findings=result.findings,
        checks=mapping_checks(result.findings, result.rows),
        mapping=mapping,
    )
