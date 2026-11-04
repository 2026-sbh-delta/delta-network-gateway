"""이전 전 매핑 판단 — 대응표로 후보와 등급을 정한다. LLM 없이 결정적으로 돈다.

approximate 는 low finding 으로 근거 문서를 붙여 설명만 받고, gap·unknown 은 사람에게 넘긴다.
방향(AWS → GCP, GCP → AWS)마다 mapping_catalog.Route 하나를 골라 같은 규칙으로 돈다.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Sequence
from typing import Any

import yaml

from delta_ai.contract import (
    Check,
    ExistingSubnet,
    Finding,
    MappingGrade,
    MappingInput,
    MappingResult,
    MappingRow,
    SourceServer,
    SourceSubnet,
    SpecOp,
    ValueOrigin,
)
from delta_ai.findings import check_state
from delta_ai.mapping_catalog import OS_SAME, ROUTES, InstanceSpec, Route
from delta_ai.mapping_cost import estimate_cost
from delta_ai.mapping_rows import RULES, Part, graded, make_finding, make_row, merge
from delta_ai.mapping_security import map_security
from delta_ai.parity import CheckOutcome
from delta_ai.resources import data_dir

MEMORY_HEADROOM = 1.2  # 사용률 제안에 남겨 둘 여유
Network = ipaddress.IPv4Network | ipaddress.IPv6Network


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
    return make_row("MAP-NET-001", "subnet", subnet.name, target, grade, origin, differences,
                    ("대상 서브넷에서 인터넷 경로와 방화벽을 실제 연결 시험으로 확인한다",))


def _map_created_subnets(route: Route, subnets: Sequence[SourceSubnet], occupied: Sequence[Network],
                         defaults: dict[str, Any]) -> tuple[Part, tuple[str, ...]]:
    used = [*occupied, *(ipaddress.ip_network(s.target_cidr) for s in subnets if s.target_cidr)]
    parts, assumptions = [], []
    for index, subnet in enumerate(subnets):
        cidr, origin = subnet.target_cidr, subnet.origin
        if cidr is None:
            cidr, origin = _free_cidr(defaults, used), "default"
            used.append(ipaddress.ip_network(cidr))
            assumptions.append(f"{subnet.name} 대상 대역 {cidr}")
        others = [*occupied, *(ipaddress.ip_network(s.target_cidr) for s in subnets[index + 1:] if s.target_cidr)]
        parts.append(_created_subnet(route, index, subnet, cidr, origin, others, defaults, used))
    return merge(parts), tuple(assumptions)


def _created_subnet(route: Route, index: int, subnet: SourceSubnet, cidr: str, origin: ValueOrigin,
                    others: Sequence[Network], defaults: dict[str, Any], used: list[Network]) -> Part:
    if subnet.tier is None:
        row = _subnet_row(subnet, f"{cidr} (공개·사설 미정)", "unknown", origin)
        part = graded(row, f"{subnet.name} 의 공개·사설 구분을 모른다")
    else:
        row = _subnet_row(subnet, f"{cidr}: {route.tier_target[subnet.tier]}", "approximate", origin,
                          (route.subnet_difference,))
        part = graded(row, route.subnet_difference)
    if not _overlaps(ipaddress.ip_network(cidr), others):
        return part
    free = _free_cidr(defaults, used)
    used.append(ipaddress.ip_network(free))  # 뒤 서브넷이 같은 대역을 받지 않게 한다
    op = SpecOp(op="replace", path=f"/mapping/subnets/{index}/target_cidr", value=free)
    conflict = make_finding("MAP-NET-002", status="fail", severity="high", ops=[op],
                            location={"subnet": subnet.name, "cidr": cidr},
                            evidence=f"{subnet.name} 의 대상 대역 {cidr} 가 출발·온프레미스·다른 대상 대역과 겹친다")
    return Part(part.rows, (conflict, *part.findings))


def _existing_for(subnet: SourceSubnet, existing: Sequence[ExistingSubnet]) -> ExistingSubnet | None:
    return next((e for e in existing if e.tier is not None and e.tier == subnet.tier), None)


def _reused_subnet(route: Route, subnet: SourceSubnet, existing: Sequence[ExistingSubnet] | None,
                   occupied: Sequence[Network]) -> Part:
    if existing is None:
        return graded(_subnet_row(subnet, None, "unknown", subnet.origin), "기존 네트워크 목록을 받지 못했다")
    match = _existing_for(subnet, existing)
    location = {"subnet": subnet.name}
    if match is None:
        evidence = f"기존 네트워크에 {subnet.tier} 서브넷이 없다"
    elif ipaddress.ip_network(match.cidr).num_addresses < ipaddress.ip_network(subnet.cidr).num_addresses:
        evidence = f"기존 서브넷 {match.name}({match.cidr}) 가 원본 {subnet.cidr} 보다 작다"
    elif _overlaps(ipaddress.ip_network(match.cidr), occupied):
        evidence = f"기존 서브넷 {match.name}({match.cidr}) 가 출발·온프레미스 대역과 겹친다"
    else:
        row = _subnet_row(subnet, f"{match.name} ({match.cidr})", "approximate", "operator", (route.subnet_difference,))
        return graded(row, route.subnet_difference)
    row = _subnet_row(subnet, match.name if match else None, "gap", subnet.origin, (evidence,))
    return Part((row,), (make_finding("MAP-NET-003", status="fail", severity="high", evidence=evidence,
                                      location=location),))


def _smallest_target(route: Route, spec: InstanceSpec, memory_gib: float) -> str | None:
    return next((name for name, target in route.target_instances.items()
                 if target.vcpus >= spec.vcpus and target.memory_gib >= memory_gib), None)


def _target_arch(route: Route) -> str:
    """대응표의 대상 후보는 한 CPU 구조로만 둔다(E2·t3 는 x86)."""
    return next(iter(route.target_instances.values())).arch


def _size_notes(route: Route, server: SourceServer, spec: InstanceSpec, chosen: str) -> tuple[str, ...]:
    target = route.target_instances[chosen]
    memory = f"메모리 {spec.memory_gib} GiB → {target.memory_gib} {route.target_memory_unit}"
    notes = [f"{memory}, vCPU {spec.vcpus} → {target.vcpus}"]
    if server.peak_memory_percent is not None:
        needed = spec.memory_gib * server.peak_memory_percent / 100 * MEMORY_HEADROOM
        smaller = _smallest_target(route, InstanceSpec(0, 0, _target_arch(route)), needed)
        if smaller and smaller != chosen and route.target_instances[smaller].memory_gib < target.memory_gib:
            notes.append(f"최대 메모리 사용률 기준 더 작은 후보 {smaller}. 제안일 뿐이며 사람이 정한다")
    return tuple(notes)


def target_type(route: Route, server: SourceServer) -> str | None:
    spec = route.source_instances.get(server.instance_type or "")
    return _smallest_target(route, spec, spec.memory_gib) if spec else None


def _map_compute(route: Route, server: SourceServer) -> Part:
    spec = route.source_instances.get(server.instance_type or "")
    chosen = target_type(route, server)
    source = f"{server.name}: {server.instance_type}"
    if chosen is None:
        row = make_row("MAP-CMP-001", "compute", source, None, "unknown", server.origin)
        return graded(row, f"대응표에 없는 서버 타입: {server.instance_type}")
    tests = (route.burst_test,) if route.target_instances[chosen].shared_core else ("대상에서 부하 시험으로 확인한다",)
    row = make_row("MAP-CMP-001", "compute", source, chosen, "approximate", server.origin,
                   _size_notes(route, server, spec, chosen), tests)
    return graded(row, f"{server.instance_type} 와 {chosen} 는 근사 대응이다")


def _map_arch(route: Route, index: int, server: SourceServer) -> Part:
    spec = route.source_instances.get(server.instance_type or "")
    if spec is None:
        return Part()  # 타입을 모르면 MAP-CMP-001 이 이미 막는다
    arch = _target_arch(route)
    source = f"{server.name}: {spec.arch}"
    if spec.arch == arch:
        return Part((make_row("MAP-CMP-002", "cpu_arch", source, arch, "exact", server.origin),))
    difference = f"{spec.arch} → {arch}. 이미지를 대상 구조로 다시 빌드해야 한다"
    row = make_row("MAP-CMP-002", "cpu_arch", source, arch, "approximate", server.origin, (difference,),
                   ("대상 구조 이미지로 스모크 시험을 돌린다",))
    if server.multi_arch_image is True:
        return graded(row, difference)
    op = SpecOp(op="add", path=f"/mapping/servers/{index}/multi_arch_image", value=True)
    fix = make_finding("MAP-CMP-002", status="fail", severity="high", ops=[op], location={"server": server.name},
                       evidence=f"{difference}. {arch} 이미지가 확인되지 않았다. 빌드한 뒤 이 값을 true 로 보낸다")
    return Part((row,), (fix,))


def _map_os(route: Route, server: SourceServer) -> Part:
    source = f"{server.name}: {server.os}"
    if server.os in OS_SAME:
        return Part((make_row("MAP-OS-001", "os", source, server.os, "exact", server.origin),))
    target = route.os_approximate.get(server.os or "")
    if target is None:
        return graded(make_row("MAP-OS-001", "os", source, None, "unknown", server.origin),
                      f"대응을 정하지 않은 OS: {server.os}")
    difference = f"{server.os} 와 같은 이미지가 대상에 없다. 패키지 이름과 부팅 스크립트가 다를 수 있다"
    row = make_row("MAP-OS-001", "os", source, target, "approximate", server.origin, (difference,),
                   ("패키지 설치와 부팅 스크립트를 대상 이미지에서 시험한다",))
    return graded(row, difference)


def _attached_identity(route: Route, server: SourceServer) -> bool | None:
    return server.instance_profile if route.source == "aws" else server.service_account


def _map_identity(route: Route, server: SourceServer) -> Part:
    source = f"{server.name}: {route.identity_source}"
    attached = _attached_identity(route, server)
    if attached is False:
        return Part((make_row("MAP-IAM-001", "identity", source, f"{route.identity_target} 권한 부여 없음", "exact",
                              server.origin),))
    if attached is None or server.permissions is None:
        row = make_row("MAP-IAM-001", "identity", source, None, "unknown", server.origin)
        return graded(row, f"{server.name} 의 권한 목록을 모른다")
    unmapped = [grant for grant in server.permissions if grant not in route.grants]
    granted = sorted({g for grant in server.permissions for g in route.grants.get(grant, ())})
    target = f"{route.identity_target}: {', '.join(granted) or '권한 없음'}"
    if unmapped:
        difference = f"자동으로 옮기지 않는 권한: {', '.join(unmapped)}"
        return graded(make_row("MAP-IAM-001", "identity", source, target, "gap", server.origin, (difference,)),
                      difference)
    difference = "정책 문법이 달라 최소 권한이 같다고 보장하지 않는다"
    row = make_row("MAP-IAM-001", "identity", source, target, "approximate", server.origin, (difference,),
                   (f"대상 {route.identity_target}으로 실제 호출을 시험한다",))
    return graded(row, difference)


def _map_network(route: Route, config: MappingInput, occupied: Sequence[Network],
                 defaults: dict[str, Any]) -> tuple[Part, tuple[str, ...]]:
    if config.network_mode == "reuse":
        return merge(_reused_subnet(route, s, config.existing_subnets, occupied) for s in config.subnets), ()
    return _map_created_subnets(route, config.subnets, occupied, defaults)


def _map_server(route: Route, index: int, server: SourceServer) -> Part:
    return merge((_map_compute(route, server), _map_arch(route, index, server), _map_os(route, server),
                  _map_identity(route, server)))


def check_mapping(config: MappingInput) -> CheckOutcome:
    route = ROUTES[(config.source_provider, config.target_provider)]
    loaded = _load_defaults()
    defaults = {**loaded, "cidr_pool": loaded["cidr_pool"][route.target]}
    reserved = (*loaded["reserved_cidrs"], *loaded["reserved_for_target"][route.target], *config.reserved_cidrs)
    occupied = [ipaddress.ip_network(cidr) for cidr in (*reserved, *(s.cidr for s in config.subnets))]
    network, cidr_notes = _map_network(route, config, occupied, defaults)
    security = map_security(config.security, config) if config.security else Part()
    result = merge((network, *(_map_server(route, i, s) for i, s in enumerate(config.servers)), security))
    region = config.region or defaults["regions"][route.target]
    source_region = config.source_region or defaults["regions"][route.source]
    cost = estimate_cost(route, [(s, target_type(route, s)) for s in config.servers], source_region, region)
    assumptions = (*(() if config.region else (f"region {region}",)),
                   *(() if config.source_region or cost is None else (f"출발 리전 {source_region}",)), *cidr_notes)
    mapping = MappingResult(region=region, network_mode=config.network_mode, rows=result.rows, assumptions=assumptions,
                            cost=cost)
    return CheckOutcome(findings=result.findings, checks=mapping_checks(result.findings, result.rows), mapping=mapping)


def mapping_checks(findings: Sequence[Finding], rows: Sequence[MappingRow]) -> tuple[Check, ...]:
    evaluated = {row.rule_id for row in rows} | {f.rule_id for f in findings}
    return tuple(Check(rule_id=rule_id, name=title, state=check_state(f for f in findings if f.rule_id == rule_id))
                 for rule_id, (_, title) in RULES.items() if rule_id in evaluated)
