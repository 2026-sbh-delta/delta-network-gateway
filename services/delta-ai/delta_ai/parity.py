"""환경 차이 규칙 — 출발·대상 인벤토리를 비교해 이전 전에 메워야 할 차이를 찾는다. LLM 없이 결정적으로 돈다."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from delta_ai.contract import (
    AttackResult,
    Check,
    EnvInventory,
    Finding,
    FirewallRule,
    PostureResult,
    ReachabilityProbe,
    Severity,
    SpecOp,
    VerifiedControl,
    WafRule,
)
from delta_ai.findings import check_state, finding_id
from delta_ai.waf_mapping import (
    AWS_GROUP_PARTIAL_NOTES,
    cloud_armor_capabilities,
    cloud_armor_rule_names,
    cloud_armor_rules,
    source_capabilities,
)

RULES: dict[str, tuple[str, str]] = {  # rule_id → (category, 제목)
    "PAR-WAF-001": ("waf", "대상 환경 WAF 동등성"),
    "PAR-WAF-002": ("waf", "대상 환경 공격 차단 시험"),
    "PAR-IAM-001": ("identity", "워크로드 권한 이식"),
    "PAR-SEC-001": ("secret", "시크릿 저장소 이식"),
    "PAR-LOG-001": ("logging", "중앙 로그 연결"),
    "PAR-ING-001": ("ingress", "인그레스 TLS"),
    "PAR-NET-001": ("network", "중앙 검사 경로"),
}
OPTIONAL_RULES: dict[str, tuple[str, str]] = {  # 입력이 있을 때만 점검 항목에 나온다
    "PAR-NET-002": ("network", "네트워크 격리 실측"),
    "PAR-NET-003": ("network", "방화벽 관리 경로 노출"),
}
NATIVE_IDENTITY = {"aws": {"irsa", "pod_identity"}, "gcp": {"workload_identity"}, "onprem": {"none"}}
SELF = "(self)"
MANAGEMENT_PORTS = {"22", "ssh", "80", "http", "www", "443", "https", "8443"}
NATIVE_SECRETS = {
    "aws": {"aws_secrets_manager", "k8s_secret", "vault"},
    "gcp": {"gcp_secret_manager", "k8s_secret", "vault"},
    "onprem": {"k8s_secret", "vault"},
}


@dataclass(frozen=True)
class CheckOutcome:
    findings: tuple[Finding, ...]
    checks: tuple[Check, ...]
    verified: tuple[VerifiedControl, ...] = ()
    posture: PostureResult | None = None


def _finding(rule_id: str, *, status: str, severity: Severity, evidence: str, location: dict[str, str] | None = None,
             ops: Sequence[SpecOp] = (), waf_rules: Sequence[WafRule] = ()) -> Finding:
    category, title = {**RULES, **OPTIONAL_RULES}[rule_id]
    where = location or {}
    return Finding(finding_id=finding_id(rule_id, where), rule_id=rule_id, category=category, severity=severity,
                   status=status, autofix="allowed" if ops or waf_rules else "forbidden", title=title,
                   location=where, evidence=evidence, ops=tuple(ops), waf_rules=tuple(waf_rules))


def _replace(path: str, value: object) -> SpecOp:
    return SpecOp(op="replace", path=path, value=value)


def _waf_fix(source: EnvInventory, target: EnvInventory, missing: frozenset[str]) -> tuple[list[SpecOp], tuple[WafRule, ...]]:
    """Cloud Armor 로 옮길 수 있을 때만 수정안을 만든다. 다른 대상은 사람이 정한다."""
    if target.provider != "gcp" or source.provider != "aws" or not missing:
        return [], ()
    current = cloud_armor_capabilities(target.waf.rule_groups or ())
    ops = [_replace("/waf/enabled", True), _replace("/waf/product", "cloud_armor"), _replace("/waf/mode", "block"),
           _replace("/waf/rule_groups", list(cloud_armor_rule_names(current | missing)))]
    return ops, cloud_armor_rules(missing)


def _source_coverage(source: EnvInventory) -> tuple[frozenset[str], tuple[str, ...]]:
    return source_capabilities(source.waf.rule_groups or ())


def _partial_notes(source: EnvInventory) -> list[Finding]:
    return [_finding("PAR-WAF-001", status="unknown", severity="low", evidence=note, location={"rule_group": group})
            for group, note in AWS_GROUP_PARTIAL_NOTES.items() if group in (source.waf.rule_groups or ())]


def _check_waf(source: EnvInventory, target: EnvInventory) -> list[Finding]:
    if source.waf.enabled is None or target.waf.enabled is None:
        return [_finding("PAR-WAF-001", status="unknown", severity="high", evidence="WAF 사용 여부를 모른다",
                         location={"field": "/waf/enabled"})]
    if not source.waf.enabled:
        return []
    if source.waf.rule_groups is None:
        return [_finding("PAR-WAF-001", status="unknown", severity="high", evidence="출발 환경 WAF 규칙 그룹을 모른다",
                         location={"field": "/waf/rule_groups"})]
    capabilities, unmapped = _source_coverage(source)
    findings = _partial_notes(source) + [
        _finding("PAR-WAF-001", status="unknown", severity="medium",
                 evidence=f"{group} 은 대상 환경 대응 규칙이 정해져 있지 않다", location={"rule_group": group})
        for group in unmapped
    ]
    if not target.waf.enabled:
        ops, rules = _waf_fix(source, target, capabilities)
        return [_finding("PAR-WAF-001", status="fail", severity="high", ops=ops, waf_rules=rules,
                         evidence=f"출발 환경은 {source.waf.product} 를 쓰는데 대상 환경에는 WAF 가 없다",
                         location={"field": "/waf/enabled"}), *findings]
    if target.waf.mode != "block":
        status = "unknown" if target.waf.mode is None else "fail"
        ops = [_replace("/waf/mode", "block")] if status == "fail" else []
        findings.append(_finding("PAR-WAF-001", status=status, severity="high", ops=ops,
                                 evidence=f"대상 환경 WAF 모드: {target.waf.mode}", location={"field": "/waf/mode"}))
    if target.waf.rule_groups is None:
        findings.append(_finding("PAR-WAF-001", status="unknown", severity="high",
                                 evidence="대상 환경 WAF 규칙 그룹을 모른다", location={"field": "/waf/rule_groups"}))
        return findings
    missing = capabilities - cloud_armor_capabilities(target.waf.rule_groups)
    if missing:
        ops, rules = _waf_fix(source, target, missing)
        findings.append(_finding("PAR-WAF-001", status="fail", severity="high", ops=ops, waf_rules=rules,
                                 evidence=f"대상 환경에 없는 범주: {', '.join(sorted(missing))}",
                                 location={"field": "/waf/rule_groups"}))
    return findings


def _check_attacks(source: EnvInventory, target: EnvInventory, attacks: Sequence[AttackResult]) -> list[Finding]:
    results = [a for a in attacks if a.environment == target.provider]
    if target.waf.enabled is False and not results:
        return []  # WAF 부터 켠 뒤 시험한다. PAR-WAF-001 이 이미 막고 있다
    if not results:
        return [_finding("PAR-WAF-002", status="unknown", severity="medium", evidence="대상 환경 공격 시험 결과가 없다")]
    findings = []
    for attack in results:
        location = {"scenario": attack.scenario}
        evidence = f"{attack.scenario}: {attack.observed} (HTTP {attack.http_status})"
        if attack.observed in ("alert_only", "unknown"):
            findings.append(_finding("PAR-WAF-002", status="unknown", severity="high", evidence=evidence, location=location))
        elif attack.observed == "allowed":
            ops, rules = ([], ())
            if target.waf.enabled is False:  # WAF 가 켜진 채 뚫렸으면 규칙 조정은 사람이 정한다
                ops, rules = _waf_fix(source, target, _source_coverage(source)[0])
            findings.append(_finding("PAR-WAF-002", status="fail", severity="high", evidence=evidence, location=location,
                                     ops=ops, waf_rules=rules))
    return findings


def _check_identity(source: EnvInventory, target: EnvInventory) -> list[Finding]:
    findings = []
    native = NATIVE_IDENTITY.get(target.provider)
    mechanism = target.identity.mechanism
    if mechanism is None or native is None:
        findings.append(_finding("PAR-IAM-001", status="unknown", severity="high",
                                 evidence=f"대상 환경 권한 방식을 판단할 수 없다: {mechanism}",
                                 location={"field": "/identity/mechanism"}))
    elif mechanism not in native:
        findings.append(_finding("PAR-IAM-001", status="fail", severity="high",
                                 evidence=f"{target.provider} 에서 쓸 수 없는 권한 방식: {mechanism}",
                                 location={"field": "/identity/mechanism"}))
    if source.provider != target.provider and target.identity.cross_cloud_access is not True:
        findings.extend(
            _finding("PAR-IAM-001", status="fail", severity="high", location={"cloud_api": api},
                     evidence=f"앱이 {source.provider} {api} 를 직접 호출하는데 대상 환경 접근이 확인되지 않았다")
            for api in source.identity.cloud_apis
        )
    return findings


def _check_secrets(target: EnvInventory) -> list[Finding]:
    findings = []
    if target.secrets.plaintext_env:
        names = ", ".join(target.secrets.plaintext_env)
        findings.append(_finding("PAR-SEC-001", status="fail", severity="high", evidence=f"평문 환경 변수: {names}",
                                 location={"field": "/secrets/plaintext_env"}))
    source = target.secrets.source
    if source is None:
        findings.append(_finding("PAR-SEC-001", status="unknown", severity="high", evidence="시크릿 저장소를 모른다",
                                 location={"field": "/secrets/source"}))
    elif source not in NATIVE_SECRETS[target.provider]:
        findings.append(_finding("PAR-SEC-001", status="fail", severity="high",
                                 evidence=f"{target.provider} 에서 값을 등록해야 하는 저장소: {source}",
                                 location={"field": "/secrets/source"}))
    return findings


def _check_flag(rule_id: str, value: bool | None, path: str, label: str, severity: Severity) -> list[Finding]:
    if value is True:
        return []
    if value is None:
        return [_finding(rule_id, status="unknown", severity=severity, evidence=f"{label} 여부를 모른다")]
    return [_finding(rule_id, status="fail", severity=severity, evidence=f"대상 환경에 {label} 이 없다",
                     ops=[_replace(path, True)])]


def _check_network(source: EnvInventory, target: EnvInventory) -> list[Finding]:
    network = target.network
    if network.inspected is None:
        return [_finding("PAR-NET-001", status="unknown", severity="high", evidence="중앙 검사 경로 통과 여부를 모른다",
                         location={"field": "/network/inspected"})]
    if not network.inspected:
        if source.network.inspected is False:
            return []
        return [_finding("PAR-NET-001", status="fail", severity="high", location={"field": "/network/inspected"},
                         evidence="대상 환경 트래픽이 중앙 검사 경로를 지나지 않는다")]
    if network.l7_visible is not True and target.waf.enabled is not True:
        return [_finding("PAR-NET-001", status="unknown", severity="low", location={"field": "/network/l7_visible"},
                         evidence=f"{network.gateway} 경로에서 L7 을 보지 못하고 WAF 도 없어 HTTP 공격을 막지 못한다")]
    return []


def _target_probes(target: EnvInventory, probes: Sequence[ReachabilityProbe]) -> list[ReachabilityProbe]:
    return [p for p in probes if p.environment == target.provider]


def _probe_subject(probe: ReachabilityProbe) -> str:
    return f"{probe.source} → {probe.target}:{probe.port}"


def _check_reachability(target: EnvInventory, probes: Sequence[ReachabilityProbe]) -> list[Finding]:
    findings = []
    for probe in _target_probes(target, probes):
        if probe.observed == probe.expected:
            continue
        subject = _probe_subject(probe)
        findings.append(_finding("PAR-NET-002", status="unknown" if probe.observed == "unknown" else "fail",
                                 severity="high", location={"probe": subject},
                                 evidence=f"{subject}: 기대 {probe.expected}, 실측 {probe.observed}"))
    return findings


def _is_firewall_address(destination: str, target: EnvInventory) -> bool:
    return destination == SELF or destination in target.network.gateway_addresses


def _exposes_firewall(rule: FirewallRule, target: EnvInventory) -> bool:
    if rule.action != "pass":
        return False
    if _is_firewall_address(rule.destination, target):
        return rule.port is None or rule.port in MANAGEMENT_PORTS or ":" in rule.port  # ":" 는 포트 범위다
    return rule.destination == "any" or rule.destination.startswith("!")  # 허용 범위에 방화벽 WAN 주소가 들어간다


def _check_firewall(target: EnvInventory) -> list[Finding]:
    rules = target.network.firewall_rules
    if rules is None:
        return []
    if not rules:
        return [_finding("PAR-NET-003", status="unknown", severity="high", evidence="방화벽 룰을 수집하지 못했다",
                         location={"field": "/network/firewall_rules"})]
    findings = []
    for index, rule in enumerate(rules):
        if rule.action == "block" and rule.destination == SELF:
            break  # 이 뒤의 허용은 방화벽 자신에 닿지 못한다
        if _exposes_firewall(rule, target):
            port = rule.port or "전 포트"
            findings.append(_finding("PAR-NET-003", status="fail", severity="high",
                                     location={"interface": rule.interface, "rule": str(index)},
                                     evidence=f"{rule.interface} 에서 {rule.source} → {rule.destination} ({port}) 허용: "
                                              "앞선 방화벽 차단이 없어 관리 포트까지 닿는다"))
    return findings


def _verified(target: EnvInventory, attacks: Sequence[AttackResult],
              probes: Sequence[ReachabilityProbe]) -> tuple[VerifiedControl, ...]:
    blocked_attacks = [VerifiedControl(rule_id="PAR-WAF-002", environment=a.environment, subject=a.scenario, observed="blocked")
                       for a in attacks if a.environment == target.provider and a.observed == "blocked"]
    closed_ports = [VerifiedControl(rule_id="PAR-NET-002", environment=p.environment, subject=_probe_subject(p), observed="closed")
                    for p in _target_probes(target, probes) if p.expected == "closed" and p.observed == "closed"]
    return (*blocked_attacks, *closed_ports)


def evaluated_network_rules(target: EnvInventory, probes: Sequence[ReachabilityProbe]) -> frozenset[str]:
    evaluated = set()
    if _target_probes(target, probes):
        evaluated.add("PAR-NET-002")
    if target.network.firewall_rules is not None:
        evaluated.add("PAR-NET-003")
    return frozenset(evaluated)


def check_parity(source: EnvInventory, target: EnvInventory, attacks: Sequence[AttackResult],
                 probes: Sequence[ReachabilityProbe] = ()) -> CheckOutcome:
    findings = (
        *_check_waf(source, target),
        *_check_attacks(source, target, attacks),
        *_check_identity(source, target),
        *_check_secrets(target),
        *_check_flag("PAR-LOG-001", target.logging.central, "/logging/central", "중앙 로그", "medium"),
        *_check_flag("PAR-ING-001", target.ingress.tls, "/ingress/tls", "TLS", "medium"),
        *_check_network(source, target),
        *_check_reachability(target, probes),
        *_check_firewall(target),
    )
    return CheckOutcome(findings=findings, checks=parity_checks(findings, evaluated_network_rules(target, probes)),
                        verified=_verified(target, attacks, probes))


def parity_checks(findings: Sequence[Finding], evaluated: frozenset[str] = frozenset()) -> tuple[Check, ...]:
    titles = {**RULES, **{rule_id: OPTIONAL_RULES[rule_id] for rule_id in OPTIONAL_RULES if rule_id in evaluated}}
    return tuple(
        Check(rule_id=rule_id, name=title, state=check_state(f for f in findings if f.rule_id == rule_id))
        for rule_id, (_, title) in titles.items()
    )
