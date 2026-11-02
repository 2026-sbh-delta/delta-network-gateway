"""네트워크 태세 판정 — 네트워크·노드·클러스터 세 계층을 실측으로 나누고, 운영자가 할 수 있는 조치를 고른다."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from delta_ai.contract import (
    Finding,
    NetworkPosture,
    PostureLayer,
    PostureLayerName,
    PostureProposal,
    PostureResult,
    ReachabilityProbe,
    Severity,
    VerifiedControl,
)
from delta_ai.findings import finding_id
from delta_ai.secrets_pattern import redact

POSTURE_RULES: dict[str, tuple[str, str]] = {
    "PAR-NET-004": ("network", "네트워크 태세"),
    "K8S-NP-001": ("cluster", "파드 간 격리"),
}
LayerState = Literal["isolated", "open", "unknown"]
OWNER = {"managed": "상위 방화벽 관리 가능", "unmanaged": "상위 방화벽 관리 불가", None: "상위 방화벽 관리 여부 모름"}
MAX_LISTED = 5
DEFAULT_GATEWAY_TARGET = "default-gateway"

_NETWORK_MANAGED = "VLAN 으로 이 노드를 분리하고, 다른 내부 대역과 방화벽 관리 포트로 가는 경로를 방화벽 룰로 막는다."
_NETWORK_UNMANAGED = ("공유 네트워크 장비는 바꿀 수 없으므로 관리 트래픽과 내부 통신을 WireGuard·Tailscale 터널로만 보내고, "
                      "내부 대역으로 나가는 연결을 노드는 호스트 방화벽으로, 파드는 Egress NetworkPolicy 로 막는다.")
_NODE_MANAGED = "상위 방화벽에서 필요한 포트만 이 노드로 허용하고, 노드 호스트 방화벽도 기본 거부로 켠다."
_NODE_UNMANAGED = ("호스트 방화벽을 기본 거부로 켜고 SSH, API 서버, kubelet 포트는 터널 인터페이스에서만 허용한다. "
                   "NodePort 는 INPUT 룰로 막히지 않으니 쓰지 않는 NodePort·LoadBalancer 를 닫고 외부에서 다시 시험한다.")
_CLUSTER_PROBE = "열린 경로에 해당하는 NetworkPolicy 를 점검하고, CNI 가 정책을 실제로 집행하는지 같은 시험으로 확인한다."
_DEFAULT_DENY = """apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: delta-default-deny-ingress
  namespace: {namespace}
spec:
  podSelector: {{}}
  policyTypes: [Ingress]
  ingress:
    - from:
        - podSelector: {{}}
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
          podSelector:
            matchLabels:
              app.kubernetes.io/name: traefik
"""


@dataclass(frozen=True)
class PostureOutcome:
    result: PostureResult
    findings: tuple[Finding, ...]
    verified: tuple[VerifiedControl, ...]


def _subject(probe: ReachabilityProbe) -> str:
    return f"{probe.source} → {probe.target}:{probe.port}"


def _controls_ok(probes: Sequence[ReachabilityProbe]) -> bool:
    return all(p.observed == "open" for p in probes if p.expected == "open")


def _opened(probes: Sequence[ReachabilityProbe]) -> list[ReachabilityProbe]:
    return [p for p in probes if p.expected == "closed" and p.observed == "open"]


def _probe_state(probes: Sequence[ReachabilityProbe]) -> LayerState:
    """열린 것은 그대로 믿는다. 닫힘은 양성 대조가 통과하고 모르는 결과가 없을 때만 믿는다."""
    if _opened(probes):
        return "open"
    must_close = [p for p in probes if p.expected == "closed"]
    if not must_close or not _controls_ok(probes) or any(p.observed == "unknown" for p in must_close):
        return "unknown"
    return "isolated"


def _is_gateway(probe: ReachabilityProbe) -> bool:
    return probe.target.split("(")[0] == DEFAULT_GATEWAY_TARGET  # 수집기는 대상 뒤에 "(주소)" 를 붙인다


def _listed(values: Sequence[str]) -> str:
    return ", ".join(values[:MAX_LISTED]) + (f" 외 {len(values) - MAX_LISTED}건" if len(values) > MAX_LISTED else "")


def _network_layer(posture: NetworkPosture) -> PostureLayer:
    probes = posture.network_probes
    state = _probe_state(probes)
    opened = [_subject(p) for p in _opened(probes)]
    if posture.node_public_ip:
        return PostureLayer(layer="network", state="open", basis=_listed(["노드에 공인 IP 있음", *opened]))
    if state == "open":
        return PostureLayer(layer="network", state="open", basis=f"막혀야 할 경로가 열림: {_listed(opened)}")
    if state == "isolated" and all(_is_gateway(p) for p in probes if p.expected == "closed"):
        return PostureLayer(layer="network", state="unknown",
                            basis="게이트웨이만 시험해 같은 망의 다른 기기로 번지는지 모름. 시험할 대상을 설정에 지정해야 한다")
    if state == "unknown" or posture.node_public_ip is None:
        return PostureLayer(layer="network", state="unknown", basis="연결 시험이나 노드 주소를 믿을 수 있게 측정하지 못함")
    return PostureLayer(layer="network", state="isolated", basis=f"막혀야 할 경로 {sum(p.expected == 'closed' for p in probes)}건 차단 실측")


def _node_layer(posture: NetworkPosture, network: LayerState) -> PostureLayer:
    ports = "대기 포트 모름" if posture.exposed_ports is None else f"대기 포트 {list(posture.exposed_ports)}"
    managed = posture.upstream_firewall == "managed"
    opened = [_subject(p) for p in _opened(posture.inbound_probes)]
    if opened:
        return PostureLayer(layer="node", state="open", basis=f"바깥에서 닿음: {_listed(opened)}, {ports}")
    guard = ("호스트 방화벽 기본 거부" if posture.host_firewall == "default_deny"
             else "상위 방화벽이 보호" if managed and network == "isolated" else None)
    if guard is None:
        if posture.host_firewall is None or (managed and network == "unknown"):
            return PostureLayer(layer="node", state="unknown", basis=f"노드를 보호하는 방화벽을 확인하지 못함, {ports}")
        return PostureLayer(layer="node", state="open", basis=f"호스트 방화벽이 기본 허용, {ports}")
    if _probe_state(posture.inbound_probes) != "isolated":
        return PostureLayer(layer="node", state="unknown", basis=f"{guard}. 다만 바깥 지점 연결 시험을 믿을 수 있게 측정하지 못함, {ports}")
    return PostureLayer(layer="node", state="isolated", basis=f"{guard}, 바깥에서 차단 실측, {ports}")


def _cluster_layer(posture: NetworkPosture) -> PostureLayer:
    uncovered = posture.namespaces_without_policy
    opened = [_subject(p) for p in _opened(posture.cluster_probes)]
    problems = [*(f"{ns} 에 수신 제한 정책 없음" for ns in uncovered or ()), *(f"{s} 열림" for s in opened)]
    if problems:
        return PostureLayer(layer="cluster", state="open", basis=_listed(problems))
    if uncovered is None or _probe_state(posture.cluster_probes) != "isolated":
        return PostureLayer(layer="cluster", state="unknown", basis="NetworkPolicy 나 파드 간 연결 시험을 확인하지 못함")
    return PostureLayer(layer="cluster", state="isolated", basis="수신 제한 정책 있음, 다른 네임스페이스 연결 차단 실측")


def _overall(network: LayerState, node: LayerState, cluster: LayerState) -> Literal["isolated", "partial", "open", "unknown"]:
    states = (network, node, cluster)
    if network == "open" and "open" in (node, cluster):
        return "open"
    if "open" in states:
        return "partial"
    return "unknown" if "unknown" in states else "isolated"


def _finding(rule_id: str, layer: PostureLayer, owner: str, severity: Severity) -> Finding | None:
    if layer.state == "isolated":
        return None
    category, title = POSTURE_RULES[rule_id]
    location = {"layer": layer.layer}
    return Finding(finding_id=finding_id(rule_id, location), rule_id=rule_id, category=category, severity=severity,
                   status="fail" if layer.state == "open" else "unknown", autofix="forbidden", title=title,
                   location=location, evidence=redact(f"{layer.layer} 계층: {layer.basis}. {owner}"))


def _track(layer: PostureLayerName, posture: NetworkPosture) -> list[PostureProposal]:
    actions = {"network": {"managed": _NETWORK_MANAGED, "unmanaged": _NETWORK_UNMANAGED},
               "node": {"managed": _NODE_MANAGED, "unmanaged": _NODE_UNMANAGED}}[layer]
    owners = [posture.upstream_firewall] if posture.upstream_firewall else ["managed", "unmanaged"]
    return [PostureProposal(layer=layer, action=actions[owner]) for owner in owners]


def _cluster_proposals(posture: NetworkPosture) -> list[PostureProposal]:
    drafts = [PostureProposal(layer="cluster", manifest=_DEFAULT_DENY.format(namespace=namespace),
                              action=f"{namespace} 에 수신 제한 정책을 적용한다. 다른 네임스페이스나 인그레스에서 오는 경로가 더 있으면 "
                                     "먼저 허용에 넣고, 적용 뒤 같은 연결 시험을 다시 돌린다.")
              for namespace in posture.namespaces_without_policy or ()]
    return drafts or [PostureProposal(layer="cluster", action=_CLUSTER_PROBE)]


def _proposals(layers: Sequence[PostureLayer], posture: NetworkPosture) -> tuple[PostureProposal, ...]:
    proposals: list[PostureProposal] = []
    for layer in layers:
        if layer.state != "open":
            continue
        proposals.extend(_cluster_proposals(posture) if layer.layer == "cluster" else _track(layer.layer, posture))
    return tuple(proposals)


def _verified(rule_id: str, probes: Sequence[ReachabilityProbe]) -> list[VerifiedControl]:
    if not _controls_ok(probes):
        return []  # 시험 도구를 믿을 수 없으면 닫힘도 근거가 되지 못한다
    return [VerifiedControl(rule_id=rule_id, environment=p.environment, subject=_subject(p), observed="closed")
            for p in probes if p.expected == "closed" and p.observed == "closed"]


def check_posture(posture: NetworkPosture) -> PostureOutcome:
    network = _network_layer(posture)
    node = _node_layer(posture, network.state)
    cluster = _cluster_layer(posture)
    layers = (network, node, cluster)
    owner = OWNER[posture.upstream_firewall]
    node_severity: Severity = "high" if network.state == "open" else "medium"
    cluster_severity: Severity = "high" if _opened(posture.cluster_probes) else "medium"
    candidates = (_finding("PAR-NET-004", network, owner, "high"), _finding("PAR-NET-004", node, owner, node_severity),
                  _finding("K8S-NP-001", cluster, owner, cluster_severity))
    result = PostureResult(overall=_overall(network.state, node.state, cluster.state),
                           upstream_firewall=posture.upstream_firewall, layers=layers,
                           proposals=_proposals(layers, posture))
    return PostureOutcome(result=result, findings=tuple(f for f in candidates if f is not None),
                          verified=(*_verified("PAR-NET-004", posture.network_probes),
                                    *_verified("PAR-NET-004", posture.inbound_probes),
                                    *_verified("K8S-NP-001", posture.cluster_probes)))
