from __future__ import annotations

from typing import Any

import pytest

from delta_ai.contract import AssessmentRequest
from delta_ai.fake_llm import FakeLLM
from delta_ai.service import assess
from delta_ai.store import RuleStore


def _probe(expected: str, observed: str, target: str = "gateway", port: int = 22) -> dict[str, Any]:
    return {"environment": "onprem", "source": "node", "target": target, "port": port, "expected": expected,
            "observed": observed}


CLOSED = _probe("closed", "closed")
OPEN = _probe("closed", "open")
CONTROL_OK = _probe("open", "open", "dns", 53)
ISOLATED: dict[str, Any] = {
    "upstream_firewall": "managed", "node_public_ip": False, "host_firewall": "permissive", "exposed_ports": [22, 6443],
    "network_probes": [CONTROL_OK, CLOSED], "inbound_probes": [CONTROL_OK, CLOSED],
    "cluster_probes": [CONTROL_OK, CLOSED], "namespaces_without_policy": [],
}
OPEN_NETWORK: dict[str, Any] = {
    "upstream_firewall": "unmanaged", "node_public_ip": True, "host_firewall": "permissive", "exposed_ports": [22, 6443],
    "network_probes": [CONTROL_OK, OPEN], "cluster_probes": [CONTROL_OK, OPEN], "namespaces_without_policy": ["demo"],
}


async def _assess(posture: dict[str, Any], tmp_path, llm: Any = None) -> Any:
    req = AssessmentRequest.model_validate({"assessment_id": "p", "kind": "runtime_security", "service": {"service_id": "s"},
                                            "evidence": {"network_posture": posture}})
    return await assess(req, llm=llm, store=RuleStore(tmp_path / "rules.json"))


def _layers(result: Any) -> dict[str, str]:
    return {layer.layer: layer.state for layer in result.posture.layers}


async def test_measured_isolation_passes_and_keeps_evidence(tmp_path):
    result = await _assess(ISOLATED, tmp_path)

    assert result.posture.overall == "isolated"
    assert result.verdict == "pass"
    assert result.findings == ()
    assert {c.rule_id for c in result.verified_controls} == {"PAR-NET-004", "K8S-NP-001"}
    assert {c.rule_id: c.state for c in result.checks} == {"PAR-NET-004": "green", "K8S-NP-001": "green"}


async def test_flat_network_without_host_firewall_is_open(tmp_path):
    result = await _assess(OPEN_NETWORK, tmp_path)

    assert result.posture.overall == "open"
    assert _layers(result) == {"network": "open", "node": "open", "cluster": "open"}
    assert result.verdict == "needs_human"
    assert {f.autofix for f in result.findings} == {"forbidden"}


async def test_collection_with_no_input_is_unknown_never_isolated(tmp_path):
    result = await _assess({}, tmp_path)

    assert result.posture.overall == "unknown"
    assert result.verdict == "needs_human"
    assert {f.status for f in result.findings} == {"unknown"}


async def test_isolated_network_with_open_cluster_is_partial(tmp_path):
    result = await _assess({**ISOLATED, "cluster_probes": [CONTROL_OK, OPEN]}, tmp_path)

    assert result.posture.overall == "partial"
    assert [f.rule_id for f in result.findings] == ["K8S-NP-001"]


async def test_namespace_without_policy_opens_cluster_even_if_probes_are_closed(tmp_path):
    result = await _assess({**ISOLATED, "namespaces_without_policy": ["demo"]}, tmp_path)

    assert _layers(result)["cluster"] == "open"
    assert "demo" in result.findings[0].evidence


async def test_failed_positive_control_makes_closed_results_untrusted(tmp_path):
    broken = _probe("open", "closed", "dns", 53)

    result = await _assess({**ISOLATED, "network_probes": [broken, CLOSED], "inbound_probes": []}, tmp_path)

    assert _layers(result)["network"] == "unknown"
    assert result.posture.overall == "unknown"
    assert not any(c.rule_id == "PAR-NET-004" for c in result.verified_controls)


async def test_gateway_only_probes_cannot_prove_lateral_isolation(tmp_path):
    gateway = _probe("closed", "closed", "default-gateway")

    result = await _assess({**ISOLATED, "network_probes": [CONTROL_OK, gateway]}, tmp_path)

    assert _layers(result)["network"] == "unknown"
    assert "같은 망" in result.posture.layers[0].basis


async def test_public_ip_opens_network_even_when_probes_are_closed(tmp_path):
    result = await _assess({**ISOLATED, "node_public_ip": True}, tmp_path)

    assert _layers(result)["network"] == "open"


async def test_host_firewall_is_not_required_only_behind_managed_isolation(tmp_path):
    managed = await _assess(ISOLATED, tmp_path)
    unmanaged = await _assess({**ISOLATED, "upstream_firewall": "unmanaged"}, tmp_path)

    assert _layers(managed)["node"] == "isolated"
    assert _layers(unmanaged)["node"] == "open"


async def test_host_firewall_protects_node_on_open_network(tmp_path):
    result = await _assess({**OPEN_NETWORK, "host_firewall": "default_deny", "inbound_probes": [CONTROL_OK, CLOSED],
                            "cluster_probes": [CONTROL_OK, CLOSED], "namespaces_without_policy": []}, tmp_path)

    assert _layers(result) == {"network": "open", "node": "isolated", "cluster": "isolated"}
    assert result.posture.overall == "partial"


async def test_nodeport_reachable_from_outside_opens_node_despite_default_deny(tmp_path):
    nodeport = _probe("closed", "open", "node", 31968)

    result = await _assess({**ISOLATED, "host_firewall": "default_deny", "inbound_probes": [CONTROL_OK, nodeport]}, tmp_path)

    assert _layers(result)["node"] == "open"
    assert "31968" in result.posture.layers[1].basis


@pytest.mark.parametrize("host_firewall", ["default_deny", "permissive"])
async def test_node_is_not_isolated_without_an_outside_measurement(tmp_path, host_firewall):
    result = await _assess({**ISOLATED, "host_firewall": host_firewall, "inbound_probes": []}, tmp_path)

    assert _layers(result)["node"] == "unknown"
    assert result.posture.overall == "unknown"


def _actions(result: Any, layer: str) -> str:
    return " ".join(p.action for p in result.posture.proposals if p.layer == layer)


async def test_managed_environment_is_told_to_change_the_firewall(tmp_path):
    result = await _assess({**OPEN_NETWORK, "upstream_firewall": "managed"}, tmp_path)

    assert "VLAN" in _actions(result, "network")
    assert "터널" not in _actions(result, "network")


async def test_unmanaged_environment_is_not_told_to_change_a_firewall_it_cannot_touch(tmp_path):
    result = await _assess(OPEN_NETWORK, tmp_path)

    assert "VLAN" not in _actions(result, "network")
    assert "터널" in _actions(result, "network")
    assert "호스트 방화벽" in _actions(result, "node")


async def test_unknown_ownership_offers_both_tracks(tmp_path):
    result = await _assess({**OPEN_NETWORK, "upstream_firewall": None}, tmp_path)

    assert "VLAN" in _actions(result, "network") and "터널" in _actions(result, "network")


async def test_cluster_proposal_has_a_policy_draft_per_uncovered_namespace(tmp_path):
    result = await _assess(OPEN_NETWORK, tmp_path)

    manifests = [p.manifest for p in result.posture.proposals if p.manifest]
    assert len(manifests) == 1
    assert "namespace: demo" in manifests[0] and "NetworkPolicy" in manifests[0]


async def test_isolated_posture_has_no_proposals(tmp_path):
    assert (await _assess(ISOLATED, tmp_path)).posture.proposals == ()


async def test_llm_cannot_change_posture(tmp_path):
    result = await _assess(OPEN_NETWORK, tmp_path, FakeLLM())

    assert result.meta.llm_used
    assert result.posture.overall == "open"
    assert result.verdict == "needs_human"


async def test_findings_tell_the_llm_who_owns_the_firewall(tmp_path):
    result = await _assess(OPEN_NETWORK, tmp_path)

    network = next(f for f in result.findings if f.location.get("layer") == "network")
    assert "상위 방화벽 관리 불가" in network.evidence


@pytest.mark.parametrize("field", ["network_probes", "cluster_probes"])
def test_probe_port_is_validated(field):
    with pytest.raises(ValueError):
        AssessmentRequest.model_validate({"assessment_id": "p", "kind": "runtime_security", "service": {"service_id": "s"},
                                          "evidence": {"network_posture": {field: [_probe("closed", "open", port=0)]}}})


async def test_result_without_posture_input_has_no_posture(tmp_path):
    req = AssessmentRequest.model_validate({"assessment_id": "p", "kind": "runtime_security", "service": {"service_id": "s"},
                                            "evidence": {"cluster_checks": []}})

    assert (await assess(req, llm=None, store=RuleStore(tmp_path / "r.json"))).posture is None
