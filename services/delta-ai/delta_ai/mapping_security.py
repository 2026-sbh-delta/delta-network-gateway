"""MAP-SEC-001 — 출발 보안 도구의 탐지 능력을 대상 도구로 덮는다.

AWS → GCP: GuardDuty 기능 → 능력 → SCC 서비스. SCC 는 Premium 이상이어야 위협 탐지가 있어 등급을 묻는다.
GCP → AWS: SCC 서비스 → 능력 → GuardDuty 기본 탐지 + 기능. GuardDuty 는 등급이 없어 묻지 않는다.
어느 쪽이든 대응을 정하지 않은 기능·서비스는 gap 이고, 사람이 accepted_gaps 로 승인해야 넘어간다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from itertools import combinations

from delta_ai.contract import FindingStatus, MappingGrade, MappingInput, SourceSecurity, ValueOrigin
from delta_ai.mapping_catalog import (
    CAPABILITY_LABELS,
    FOUNDATION,
    GUARDDUTY_FEATURE_CANDIDATES,
    GUARDDUTY_FOUNDATION_CAPABILITIES,
    LOG_THREATS,
    PLAN_CAPABILITIES,
    SCC_SERVICES,
)
from delta_ai.mapping_rows import Part, graded, make_finding, make_row, merge

SAME_DETECTION = "제품이 달라 같은 탐지를 보장하지 않는다"
SAMPLE_TEST = "대상에서 샘플 탐지가 나오는지 시험한다"
VM_AGENT = "VM Threat Detection 은 에이전트 없이 메모리를 보지만 GuardDuty Runtime Monitoring 은 에이전트가 있어야 한다"


def smallest_cover(needed: frozenset[str], services: Mapping[str, Iterable[str]]) -> tuple[str, ...]:
    """needed 를 덮는 가장 적은 서비스 조합. 같은 크기면 services 의 앞 순서를 고른다."""
    if not needed:
        return ()
    for size in range(1, len(services) + 1):
        for names in combinations(services, size):
            if needed <= frozenset().union(*(frozenset(services[name]) for name in names)):
                return names
    raise ValueError(f"덮을 수 없는 능력: {sorted(needed)}")


def _security_gap(source: str, capability: str, reason: str, config: MappingInput, origin: ValueOrigin) -> Part:
    accepted = capability in config.accepted_gaps
    row = make_row("MAP-SEC-001", "security", source, None, "gap", "operator" if accepted else origin, (reason,))
    status: FindingStatus = "dismissed" if accepted else "fail"
    finding = make_finding("MAP-SEC-001", status=status, severity="high", evidence=reason,
                           location={"capability": capability})
    return Part((row,), (finding,))


def _unknown(source: str, evidence: str, origin: ValueOrigin) -> Part:
    return graded(make_row("MAP-SEC-001", "security", source, None, "unknown", origin), evidence)


# --- AWS → GCP -----------------------------------------------------------------------------------------------------


def _premium_gap(capability: str, config: MappingInput, origin: ValueOrigin) -> Part:
    """Premium 이 없어 잃는 능력 하나를 한 행으로 낸다. 행마다 어떤 능력·서비스인지 보여야 구분된다."""
    service = next(name for name, capabilities in SCC_SERVICES.items() if capability in capabilities)
    label = CAPABILITY_LABELS.get(capability, capability)
    reason = f"Security Command Center Premium 이 아니어서 {service} 를 쓸 수 없다. {label} 능력을 잃는다"
    return _security_gap(f"GuardDuty {label}", capability, reason, config, origin)


def _scc_cover(security: SourceSecurity, covered: frozenset[str], config: MappingInput) -> Part:
    names = smallest_cover(covered, SCC_SERVICES)
    if config.scc_tier is None:
        row = make_row("MAP-SEC-001", "security", FOUNDATION, ", ".join(names), "unknown", security.origin)
        return graded(row, "대상 프로젝트의 Security Command Center 등급을 모른다")
    if config.scc_tier == "standard":
        return merge(_premium_gap(capability, config, security.origin) for capability in sorted(covered))
    grade: MappingGrade = "split" if len(names) > 1 else "approximate"
    tests = ("탐지가 쓰는 감사 로그가 켜져 있는지 확인한다", SAMPLE_TEST)
    row = make_row("MAP-SEC-001", "security", FOUNDATION, ", ".join(names), grade, security.origin, (SAME_DETECTION,),
                   tests)
    return graded(row, f"GuardDuty 능력을 {', '.join(names)} 로 나눠 대응한다")


def _map_guardduty(security: SourceSecurity, config: MappingInput) -> Part:
    covered = {LOG_THREATS}
    parts = []
    for plan in security.guardduty_plans:
        capabilities = PLAN_CAPABILITIES.get(plan)
        if capabilities is None:
            reason = (f"GuardDuty {plan} 의 대응을 정하지 않아 이 능력을 잃는다. "
                      "그 서비스를 대상에서 쓰지 않으면 사람이 accepted_gaps 로 승인한다")
            parts.append(_security_gap(plan, plan, reason, config, security.origin))
        else:
            covered.update(capabilities)
    return merge((_scc_cover(security, frozenset(covered), config), *parts))


def _from_aws(security: SourceSecurity, config: MappingInput) -> list[Part]:
    if security.guardduty_enabled is None:
        return [_unknown("GuardDuty", "GuardDuty 사용 여부를 모른다. Terraform 밖에서 켰을 수 있다", security.origin)]
    return [_map_guardduty(security, config)] if security.guardduty_enabled else []


# --- GCP → AWS -----------------------------------------------------------------------------------------------------


def guardduty_features(needed: frozenset[str]) -> tuple[str, ...]:
    """기본 탐지가 못 덮는 능력을 덮는 GuardDuty 기능. 파이프라인이 켤 기능 목록으로 쓴다."""
    candidates = {name: PLAN_CAPABILITIES[name] for name in GUARDDUTY_FEATURE_CANDIDATES}
    return smallest_cover(needed - GUARDDUTY_FOUNDATION_CAPABILITIES, candidates)


def _guardduty_cover(security: SourceSecurity, services: tuple[str, ...], needed: frozenset[str]) -> Part:
    source = f"Security Command Center: {', '.join(services) or '위협 탐지 없음'}"
    if not needed:
        row = make_row("MAP-SEC-001", "security", source, "옮길 위협 탐지 없음", "exact", security.origin)
        return Part((row,))
    features = guardduty_features(needed)
    target = ", ".join((FOUNDATION, *features))
    differences = [SAME_DETECTION, *((VM_AGENT,) if "vm_threats" in needed else ())]
    tests = ["CloudTrail·VPC 흐름 로그·DNS 로그가 GuardDuty 에 들어오는지 확인한다", SAMPLE_TEST]
    if "RUNTIME_MONITORING" in features:
        tests.insert(1, "Runtime Monitoring 에이전트가 대상 인스턴스에서 돌고 있는지 확인한다")
    grade: MappingGrade = "split" if features else "approximate"
    row = make_row("MAP-SEC-001", "security", source, target, grade, security.origin, differences, tests)
    return graded(row, f"SCC 능력을 {target} 로 대응한다")


def _from_gcp(security: SourceSecurity, config: MappingInput) -> list[Part]:
    if security.scc_services is None:
        return [_unknown("Security Command Center", "출발 프로젝트에서 켠 SCC 서비스를 모른다", security.origin)]
    needed: set[str] = set()
    parts = []
    for service in security.scc_services:
        capabilities = SCC_SERVICES.get(service)
        if capabilities is None:
            reason = (f"SCC {service} 의 대응을 정하지 않아 이 능력을 잃는다. "
                      "그 서비스를 대상에서 쓰지 않으면 사람이 accepted_gaps 로 승인한다")
            parts.append(_security_gap(service, service, reason, config, security.origin))
        else:
            needed.update(capabilities)
    mapped = tuple(s for s in security.scc_services if s in SCC_SERVICES)
    return [_guardduty_cover(security, mapped, frozenset(needed)), *parts]


def map_security(security: SourceSecurity, config: MappingInput) -> Part:
    parts = [_unknown(tool, f"대응을 정하지 않은 도구: {tool}", security.origin) for tool in security.other_tools]
    parts.extend(_from_aws(security, config) if config.source_provider == "aws" else _from_gcp(security, config))
    return merge(parts)
