"""이전 전 매핑 판단 — 대응표로 후보와 등급을 정한다. LLM 없이 결정적으로 돈다.

approximate 는 low finding 으로 근거 문서를 붙여 설명만 받고, gap·unknown 은 사람에게 넘긴다.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import Any

import yaml

from delta_ai.contract import (
    Check,
    ExistingSubnet,
    Finding,
    FindingStatus,
    MappingGrade,
    MappingInput,
    MappingResult,
    MappingRow,
    Severity,
    SourceSecurity,
    SourceServer,
    SourceSubnet,
    SpecOp,
    ValueOrigin,
)
from delta_ai.findings import check_state, finding_id
from delta_ai.mapping_catalog import (
    AWS_ACTION_ROLES,
    AWS_INSTANCES,
    FOUNDATION,
    GCP_INSTANCES,
    LOG_THREATS,
    OS_APPROXIMATE,
    OS_SAME,
    PLAN_CAPABILITIES,
    SCC_SERVICES,
    InstanceSpec,
)
from delta_ai.parity import CheckOutcome
from delta_ai.resources import data_dir

RULES: dict[str, tuple[str, str]] = {  # rule_id → (category, 제목)
    "MAP-NET-001": ("network", "서브넷 구조 대응"),
    "MAP-NET-002": ("network", "대상 대역 충돌"),
    "MAP-NET-003": ("network", "기존 네트워크 대조"),
    "MAP-CMP-001": ("compute", "서버 스펙 대응"),
    "MAP-CMP-002": ("compute", "CPU 구조 변경"),
    "MAP-OS-001": ("compute", "운영체제 대응"),
    "MAP-IAM-001": ("identity", "권한 대응"),
    "MAP-SEC-001": ("security", "보안 도구 능력 대응"),
}
TARGET_ARCH = "x86_64"  # 대응표의 E2 는 전부 x86 계열이다
LOAD_TEST = "공유 코어라 vCPU 수만으로 같다고 보지 않는다. 대상에서 부하 시험으로 확인한다"
MEMORY_HEADROOM = 1.2  # 사용률 제안에 남겨 둘 여유
TIER_TARGET = {
    "public": "VPC 서브넷 + 외부 IP + 기본 인터넷 경로 + 방화벽",
    "private": "VPC 서브넷 + 외부 IP 없음 + 필요 시 Cloud NAT",
}
SUBNET_DIFFERENCE = "AWS 서브넷은 AZ 단위이고 경로로 공개·사설을 나눈다. GCP 서브넷은 리전 단위이고 공개·사설 구분이 없다"
Network = ipaddress.IPv4Network | ipaddress.IPv6Network


@dataclass(frozen=True)
class _Part:
    rows: tuple[MappingRow, ...] = ()
    findings: tuple[Finding, ...] = ()


def _finding(rule_id: str, *, status: FindingStatus, severity: Severity, evidence: str, location: dict[str, str],
             ops: Sequence[SpecOp] = ()) -> Finding:
    category, title = RULES[rule_id]
    return Finding(finding_id=finding_id(rule_id, location), rule_id=rule_id, category=category, severity=severity,
                   status=status, autofix="allowed" if ops else "forbidden", title=title, location=location,
                   evidence=evidence, ops=tuple(ops))


def _row(rule_id: str, resource: str, source: str, target: str | None, grade: MappingGrade, origin: ValueOrigin,
         differences: Iterable[str] = (), tests: Iterable[str] = ()) -> MappingRow:
    return MappingRow(resource=resource, source=source, target=target, grade=grade, differences=tuple(differences),
                      required_tests=tuple(tests), value_source=origin, rule_id=rule_id)


def _graded(row: MappingRow, evidence: str) -> _Part:
    """등급에 맞는 finding 을 붙인다. approximate·split 은 막지 않고 설명만 받는다."""
    location = {"resource": row.resource, "source": row.source}
    if row.grade in ("approximate", "split"):
        finding = _finding(row.rule_id, status="unknown", severity="low", evidence=evidence, location=location)
    elif row.grade == "gap":
        finding = _finding(row.rule_id, status="fail", severity="high", evidence=evidence, location=location)
    elif row.grade == "unknown":
        finding = _finding(row.rule_id, status="unknown", severity="high", evidence=evidence, location=location)
    else:
        return _Part((row,))
    return _Part((row,), (finding,))


def _merge(parts: Iterable[_Part]) -> _Part:
    parts = tuple(parts)
    return _Part(tuple(r for p in parts for r in p.rows), tuple(f for p in parts for f in p.findings))


def _load_defaults() -> dict[str, Any]:
    return yaml.safe_load((data_dir("knowledge") / "mapping" / "defaults.yaml").read_text(encoding="utf-8"))


def _overlaps(network: Network, others: Iterable[Network]) -> bool:
    return any(network.overlaps(other) for other in others)


def _free_cidr(defaults: dict[str, Any], used: Sequence[Network]) -> str:
    pool = ipaddress.ip_network(defaults["cidr_pool"])
    for candidate in pool.subnets(new_prefix=defaults["subnet_prefix"]):
        if not _overlaps(candidate, used):
            return str(candidate)
    raise ValueError(f"기본값 대역 {pool} 에 남은 서브넷이 없다")


def _subnet_row(subnet: SourceSubnet, target: str | None, grade: MappingGrade, origin: ValueOrigin,
                differences: Iterable[str] = ()) -> MappingRow:
    return _row("MAP-NET-001", "subnet", subnet.name, target, grade, origin, differences,
                ("대상 서브넷에서 인터넷 경로와 방화벽을 실제 연결 시험으로 확인한다",))


def _map_created_subnets(subnets: Sequence[SourceSubnet], occupied: Sequence[Network],
                         defaults: dict[str, Any]) -> tuple[_Part, tuple[str, ...]]:
    used = [*occupied, *(ipaddress.ip_network(s.target_cidr) for s in subnets if s.target_cidr)]
    parts, assumptions = [], []
    for index, subnet in enumerate(subnets):
        cidr, origin = subnet.target_cidr, subnet.origin
        if cidr is None:
            cidr, origin = _free_cidr(defaults, used), "default"
            used.append(ipaddress.ip_network(cidr))
            assumptions.append(f"{subnet.name} 대상 대역 {cidr}")
        others = [*occupied, *(ipaddress.ip_network(s.target_cidr) for s in subnets[index + 1:] if s.target_cidr)]
        parts.append(_created_subnet(index, subnet, cidr, origin, others, defaults, used))
    return _merge(parts), tuple(assumptions)


def _created_subnet(index: int, subnet: SourceSubnet, cidr: str, origin: ValueOrigin, others: Sequence[Network],
                    defaults: dict[str, Any], used: list[Network]) -> _Part:
    if subnet.tier is None:
        row = _subnet_row(subnet, f"{cidr} (공개·사설 미정)", "unknown", origin)
        part = _graded(row, f"{subnet.name} 의 공개·사설 구분을 모른다")
    else:
        row = _subnet_row(subnet, f"{cidr}: {TIER_TARGET[subnet.tier]}", "approximate", origin, (SUBNET_DIFFERENCE,))
        part = _graded(row, SUBNET_DIFFERENCE)
    if not _overlaps(ipaddress.ip_network(cidr), others):
        return part
    free = _free_cidr(defaults, used)
    used.append(ipaddress.ip_network(free))  # 뒤 서브넷이 같은 대역을 받지 않게 한다
    op = SpecOp(op="replace", path=f"/mapping/subnets/{index}/target_cidr", value=free)
    conflict = _finding("MAP-NET-002", status="fail", severity="high", ops=[op],
                        location={"subnet": subnet.name, "cidr": cidr},
                        evidence=f"{subnet.name} 의 대상 대역 {cidr} 가 출발·온프레미스·다른 대상 대역과 겹친다")
    return _Part(part.rows, (conflict, *part.findings))


def _existing_for(subnet: SourceSubnet, existing: Sequence[ExistingSubnet]) -> ExistingSubnet | None:
    return next((e for e in existing if e.tier is not None and e.tier == subnet.tier), None)


def _reused_subnet(subnet: SourceSubnet, existing: Sequence[ExistingSubnet] | None, occupied: Sequence[Network]) -> _Part:
    if existing is None:
        return _graded(_subnet_row(subnet, None, "unknown", subnet.origin), "기존 네트워크 목록을 받지 못했다")
    match = _existing_for(subnet, existing)
    location = {"subnet": subnet.name}
    if match is None:
        evidence = f"기존 네트워크에 {subnet.tier} 서브넷이 없다"
    elif ipaddress.ip_network(match.cidr).num_addresses < ipaddress.ip_network(subnet.cidr).num_addresses:
        evidence = f"기존 서브넷 {match.name}({match.cidr}) 가 원본 {subnet.cidr} 보다 작다"
    elif _overlaps(ipaddress.ip_network(match.cidr), occupied):
        evidence = f"기존 서브넷 {match.name}({match.cidr}) 가 출발·온프레미스 대역과 겹친다"
    else:
        row = _subnet_row(subnet, f"{match.name} ({match.cidr})", "approximate", "operator", (SUBNET_DIFFERENCE,))
        return _graded(row, SUBNET_DIFFERENCE)
    row = _subnet_row(subnet, match.name if match else None, "gap", subnet.origin, (evidence,))
    return _Part((row,), (_finding("MAP-NET-003", status="fail", severity="high", evidence=evidence, location=location),))


def _smallest_target(spec: InstanceSpec, memory_gib: float) -> str | None:
    return next((name for name, target in GCP_INSTANCES.items()
                 if target.vcpus >= spec.vcpus and target.memory_gib >= memory_gib), None)


def _size_notes(server: SourceServer, spec: InstanceSpec, chosen: str) -> tuple[str, ...]:
    target = GCP_INSTANCES[chosen]
    notes = [f"메모리 {spec.memory_gib} GiB → {target.memory_gib} GB, vCPU {spec.vcpus} → {target.vcpus}"]
    if server.peak_memory_percent is not None:
        needed = spec.memory_gib * server.peak_memory_percent / 100 * MEMORY_HEADROOM
        smaller = _smallest_target(InstanceSpec(0, 0, TARGET_ARCH), needed)
        if smaller and smaller != chosen and GCP_INSTANCES[smaller].memory_gib < target.memory_gib:
            notes.append(f"최대 메모리 사용률 기준 더 작은 후보 {smaller}. 제안일 뿐이며 사람이 정한다")
    return tuple(notes)


def _map_compute(server: SourceServer) -> _Part:
    spec = AWS_INSTANCES.get(server.instance_type or "")
    chosen = _smallest_target(spec, spec.memory_gib) if spec else None
    if chosen is None:
        row = _row("MAP-CMP-001", "compute", f"{server.name}: {server.instance_type}", None, "unknown", server.origin)
        return _graded(row, f"대응표에 없는 서버 타입: {server.instance_type}")
    tests = (LOAD_TEST,) if GCP_INSTANCES[chosen].shared_core else ("대상에서 부하 시험으로 확인한다",)
    row = _row("MAP-CMP-001", "compute", f"{server.name}: {server.instance_type}", chosen, "approximate", server.origin,
               _size_notes(server, spec, chosen), tests)
    return _graded(row, f"{server.instance_type} 와 {chosen} 는 근사 대응이다")


def _map_arch(index: int, server: SourceServer) -> _Part:
    spec = AWS_INSTANCES.get(server.instance_type or "")
    if spec is None:
        return _Part()  # 타입을 모르면 MAP-CMP-001 이 이미 막는다
    source = f"{server.name}: {spec.arch}"
    if spec.arch == TARGET_ARCH:
        return _Part((_row("MAP-CMP-002", "cpu_arch", source, TARGET_ARCH, "exact", server.origin),))
    difference = f"{spec.arch} → {TARGET_ARCH}. 이미지를 대상 구조로 다시 빌드해야 한다"
    row = _row("MAP-CMP-002", "cpu_arch", source, TARGET_ARCH, "approximate", server.origin, (difference,),
               ("대상 구조 이미지로 스모크 시험을 돌린다",))
    if server.multi_arch_image is True:
        return _graded(row, difference)
    op = SpecOp(op="add", path=f"/mapping/servers/{index}/multi_arch_image", value=True)
    fix = _finding("MAP-CMP-002", status="fail", severity="high", ops=[op], location={"server": server.name},
                   evidence=f"{difference}. {TARGET_ARCH} 이미지가 확인되지 않았다. 빌드한 뒤 이 값을 true 로 보낸다")
    return _Part((row,), (fix,))


def _map_os(server: SourceServer) -> _Part:
    source = f"{server.name}: {server.os}"
    if server.os in OS_SAME:
        return _Part((_row("MAP-OS-001", "os", source, server.os, "exact", server.origin),))
    target = OS_APPROXIMATE.get(server.os or "")
    if target is None:
        return _graded(_row("MAP-OS-001", "os", source, None, "unknown", server.origin), f"대응을 정하지 않은 OS: {server.os}")
    difference = f"{server.os} 와 같은 이미지가 대상에 없다. 패키지 이름과 부팅 스크립트가 다를 수 있다"
    row = _row("MAP-OS-001", "os", source, target, "approximate", server.origin, (difference,),
               ("패키지 설치와 부팅 스크립트를 대상 이미지에서 시험한다",))
    return _graded(row, difference)


def _map_identity(server: SourceServer) -> _Part:
    source = f"{server.name}: 인스턴스 프로파일"
    if server.instance_profile is False:
        return _Part((_row("MAP-IAM-001", "identity", source, "서비스 계정 권한 부여 없음", "exact", server.origin),))
    if server.instance_profile is None or server.permissions is None:
        row = _row("MAP-IAM-001", "identity", source, None, "unknown", server.origin)
        return _graded(row, f"{server.name} 의 권한 목록을 모른다")
    unmapped = [action for action in server.permissions if action not in AWS_ACTION_ROLES]
    roles = sorted({AWS_ACTION_ROLES[action] for action in server.permissions if action in AWS_ACTION_ROLES})
    target = f"서비스 계정: {', '.join(roles) or '역할 없음'}"
    if unmapped:
        difference = f"자동으로 옮기지 않는 권한: {', '.join(unmapped)}"
        return _graded(_row("MAP-IAM-001", "identity", source, target, "gap", server.origin, (difference,)), difference)
    difference = "정책 문법이 달라 최소 권한이 같다고 보장하지 않는다"
    row = _row("MAP-IAM-001", "identity", source, target, "approximate", server.origin, (difference,),
               ("대상 서비스 계정으로 실제 호출을 시험한다",))
    return _graded(row, difference)


def _smallest_cover(needed: frozenset[str]) -> tuple[str, ...]:
    for size in range(1, len(SCC_SERVICES) + 1):
        for names in combinations(SCC_SERVICES, size):
            if needed <= frozenset().union(*(SCC_SERVICES[name] for name in names)):
                return names
    raise ValueError(f"덮을 수 없는 능력: {sorted(needed)}")


def _security_gap(source: str, capability: str, reason: str, config: MappingInput, origin: ValueOrigin) -> _Part:
    accepted = capability in config.accepted_gaps
    row = _row("MAP-SEC-001", "security", source, None, "gap", "operator" if accepted else origin, (reason,))
    status: FindingStatus = "dismissed" if accepted else "fail"
    finding = _finding("MAP-SEC-001", status=status, severity="high", evidence=reason,
                       location={"capability": capability})
    return _Part((row,), (finding,))


def _security_cover(security: SourceSecurity, covered: frozenset[str], config: MappingInput) -> _Part:
    names = _smallest_cover(covered)
    if config.scc_tier is None:
        row = _row("MAP-SEC-001", "security", FOUNDATION, ", ".join(names), "unknown", security.origin)
        return _graded(row, "대상 프로젝트의 Security Command Center 등급을 모른다")
    if config.scc_tier == "standard":
        reason = f"Security Command Center Premium 이 아니어서 쓸 수 없다: {', '.join(names)}"
        return _merge(_security_gap(FOUNDATION, capability, reason, config, security.origin) for capability in sorted(covered))
    differences = ["제품이 달라 같은 탐지를 보장하지 않는다"]
    grade: MappingGrade = "split" if len(names) > 1 else "approximate"
    tests = ("탐지가 쓰는 감사 로그가 켜져 있는지 확인한다", "대상에서 샘플 탐지가 나오는지 시험한다")
    row = _row("MAP-SEC-001", "security", FOUNDATION, ", ".join(names), grade, security.origin, differences, tests)
    return _graded(row, f"GuardDuty 능력을 {', '.join(names)} 로 나눠 대응한다")


def _map_guardduty(security: SourceSecurity, config: MappingInput) -> _Part:
    covered = {LOG_THREATS}
    parts = []
    for plan in security.guardduty_plans:
        capabilities = PLAN_CAPABILITIES.get(plan)
        if capabilities is None:
            reason = f"GuardDuty {plan} 의 대응을 정하지 않아 이 능력을 잃는다"
            parts.append(_security_gap(plan, plan, reason, config, security.origin))
        else:
            covered.update(capabilities)
    return _merge((_security_cover(security, frozenset(covered), config), *parts))


def _map_security(security: SourceSecurity, config: MappingInput) -> _Part:
    parts = [_graded(_row("MAP-SEC-001", "security", tool, None, "unknown", security.origin), f"대응을 정하지 않은 도구: {tool}")
             for tool in security.other_tools]
    if security.guardduty_enabled is None:
        row = _row("MAP-SEC-001", "security", "GuardDuty", None, "unknown", security.origin)
        parts.append(_graded(row, "GuardDuty 사용 여부를 모른다. Terraform 밖에서 켰을 수 있다"))
    elif security.guardduty_enabled:
        parts.append(_map_guardduty(security, config))
    return _merge(parts)


def _map_network(config: MappingInput, occupied: Sequence[Network], defaults: dict[str, Any]) -> tuple[_Part, tuple[str, ...]]:
    if config.network_mode == "reuse":
        return _merge(_reused_subnet(s, config.existing_subnets, occupied) for s in config.subnets), ()
    return _map_created_subnets(config.subnets, occupied, defaults)


def _map_server(index: int, server: SourceServer) -> _Part:
    return _merge((_map_compute(server), _map_arch(index, server), _map_os(server), _map_identity(server)))


def check_mapping(config: MappingInput) -> CheckOutcome:
    defaults = _load_defaults()
    reserved = (*defaults["reserved_cidrs"], *config.reserved_cidrs)
    occupied = [ipaddress.ip_network(cidr) for cidr in (*reserved, *(s.cidr for s in config.subnets))]
    network, cidr_notes = _map_network(config, occupied, defaults)
    security = _map_security(config.security, config) if config.security else _Part()
    result = _merge((network, *(_map_server(i, s) for i, s in enumerate(config.servers)), security))
    assumptions = cidr_notes if config.region else (f"region {defaults['region']}", *cidr_notes)
    mapping = MappingResult(region=config.region or defaults["region"], network_mode=config.network_mode,
                            rows=result.rows, assumptions=assumptions)
    return CheckOutcome(findings=result.findings, checks=mapping_checks(result.findings, result.rows), mapping=mapping)


def mapping_checks(findings: Sequence[Finding], rows: Sequence[MappingRow]) -> tuple[Check, ...]:
    evaluated = {row.rule_id for row in rows} | {f.rule_id for f in findings}
    return tuple(Check(rule_id=rule_id, name=title, state=check_state(f for f in findings if f.rule_id == rule_id))
                 for rule_id, (_, title) in RULES.items() if rule_id in evaluated)
