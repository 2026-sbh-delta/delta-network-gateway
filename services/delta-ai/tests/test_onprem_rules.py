from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError

from delta_ai.contract import AssessmentRequest
from delta_ai.service import assess
from delta_ai.store import RuleStore

SECURE_TARGET: dict[str, Any] = {
    "provider": "onprem",
    "waf": {"enabled": False},
    "identity": {"mechanism": "none"},
    "secrets": {"source": "k8s_secret"},
    "logging": {"central": True, "destination": "central"},
    "ingress": {"tls": True},
    "network": {"gateway": "anf", "inspected": True, "l7_visible": False},
}
SOURCE: dict[str, Any] = {"provider": "onprem", "waf": {"enabled": False}, "network": {"inspected": True}}


def _request(target: dict[str, Any], **evidence: Any) -> AssessmentRequest:
    return AssessmentRequest.model_validate({
        "assessment_id": "a", "kind": "migration_parity", "service": {"service_id": "s"},
        "source_env": SOURCE, "target_env": target, "evidence": evidence,
    })


def _target(**network: Any) -> dict[str, Any]:
    target = copy.deepcopy(SECURE_TARGET)
    target["network"].update(network)
    return target


def _probe(expected: str, observed: str, port: int = 22) -> dict[str, Any]:
    return {"environment": "onprem", "source": "vm", "target": "gateway", "port": port,
            "expected": expected, "observed": observed}


def _rule(action: str, destination: str, *, protocol: str = "any", port: str | None = None) -> dict[str, Any]:
    return {"interface": "vlan04", "action": action, "protocol": protocol, "source": "any",
            "destination": destination, "port": port}


async def _assess(req: AssessmentRequest, tmp_path) -> Any:
    return await assess(req, llm=None, store=RuleStore(tmp_path / "rules.json"))


def _by_rule(result: Any, rule_id: str) -> list[Any]:
    return [f for f in result.findings if f.rule_id == rule_id]


def _check(result: Any, rule_id: str) -> str | None:
    return next((c.state for c in result.checks if c.rule_id == rule_id), None)


@pytest.mark.parametrize(("mechanism", "status"), [("none", None), ("static_key", "fail"), (None, "unknown")])
async def test_onprem_identity_is_judged_instead_of_always_unknown(tmp_path, mechanism, status):
    target = copy.deepcopy(SECURE_TARGET)
    target["identity"] = {"mechanism": mechanism}

    found = _by_rule(await _assess(_request(target), tmp_path), "PAR-IAM-001")

    assert [f.status for f in found] == ([status] if status else [])


async def test_secure_onprem_target_passes(tmp_path):
    result = await _assess(_request(_target()), tmp_path)

    assert result.verdict == "pass"


async def test_open_port_that_should_be_closed_blocks_with_human_review(tmp_path):
    result = await _assess(_request(_target(), reachability=[_probe("closed", "open")]), tmp_path)

    finding = _by_rule(result, "PAR-NET-002")[0]
    assert (finding.status, finding.severity, finding.autofix) == ("fail", "high", "forbidden")
    assert result.verdict == "needs_human"
    assert _check(result, "PAR-NET-002") == "red"


async def test_closed_port_that_should_be_open_is_a_failure(tmp_path):
    result = await _assess(_request(_target(), reachability=[_probe("open", "closed", 53)]), tmp_path)

    assert _by_rule(result, "PAR-NET-002")[0].status == "fail"


async def test_unknown_probe_is_not_treated_as_closed(tmp_path):
    result = await _assess(_request(_target(), reachability=[_probe("closed", "unknown")]), tmp_path)

    assert _by_rule(result, "PAR-NET-002")[0].status == "unknown"
    assert result.verified_controls == ()


async def test_blocked_probes_and_attacks_are_recorded_as_verified_controls(tmp_path):
    attack = {"scenario": "sqli", "environment": "onprem", "observed": "blocked", "http_status": 403}

    result = await _assess(_request(_target(), reachability=[_probe("closed", "closed")], attack_results=[attack]),
                           tmp_path)

    assert result.verdict == "pass"
    assert {(c.rule_id, c.subject, c.observed) for c in result.verified_controls} == {
        ("PAR-NET-002", "vm → gateway:22", "closed"), ("PAR-WAF-002", "sqli", "blocked")}
    assert _check(result, "PAR-NET-002") == "green"


async def test_probes_for_other_environments_are_ignored(tmp_path):
    other = {**_probe("closed", "open"), "environment": "aws"}

    result = await _assess(_request(_target(), reachability=[other]), tmp_path)

    assert _by_rule(result, "PAR-NET-002") == []
    assert _check(result, "PAR-NET-002") is None


async def test_network_checks_are_hidden_when_not_evaluated(tmp_path):
    result = await _assess(_request(_target()), tmp_path)

    assert {"PAR-NET-002", "PAR-NET-003"}.isdisjoint(c.rule_id for c in result.checks)


async def test_gateway_rule_allowing_every_port_fails(tmp_path):
    rules = [_rule("pass", "192.0.2.1")]

    result = await _assess(_request(_target(gateway_addresses=["192.0.2.1"], firewall_rules=rules)), tmp_path)

    finding = _by_rule(result, "PAR-NET-003")[0]
    assert (finding.status, finding.severity, finding.autofix) == ("fail", "high", "forbidden")


async def test_broad_allow_without_earlier_firewall_block_fails(tmp_path):
    rules = [_rule("pass", "any")]

    result = await _assess(_request(_target(gateway_addresses=["192.0.2.1"], firewall_rules=rules)), tmp_path)

    assert _by_rule(result, "PAR-NET-003")[0].status == "fail"


async def test_narrow_gateway_rules_and_firewall_block_pass(tmp_path):
    rules = [
        _rule("pass", "192.0.2.1", protocol="udp", port="domain"),
        _rule("block", "(self)"),
        _rule("pass", "any"),
    ]

    result = await _assess(_request(_target(gateway_addresses=["192.0.2.1"], firewall_rules=rules)), tmp_path)

    assert _by_rule(result, "PAR-NET-003") == []
    assert _check(result, "PAR-NET-003") == "green"


@pytest.mark.parametrize("port", ["ssh", "https", "1:65535"])
async def test_gateway_rule_opening_management_port_fails(tmp_path, port):
    rules = [_rule("pass", "192.0.2.1", protocol="tcp", port=port)]

    result = await _assess(_request(_target(gateway_addresses=["192.0.2.1"], firewall_rules=rules)), tmp_path)

    assert _by_rule(result, "PAR-NET-003")[0].status == "fail"


async def test_block_rule_after_broad_allow_does_not_protect(tmp_path):
    rules = [_rule("pass", "any"), _rule("block", "(self)")]

    result = await _assess(_request(_target(gateway_addresses=["192.0.2.1"], firewall_rules=rules)), tmp_path)

    assert _by_rule(result, "PAR-NET-003")[0].status == "fail"


async def test_empty_firewall_rules_are_unknown_not_safe(tmp_path):
    result = await _assess(_request(_target(firewall_rules=[])), tmp_path)

    assert _by_rule(result, "PAR-NET-003")[0].status == "unknown"


def test_probe_rejects_invalid_port():
    with pytest.raises(ValidationError):
        _request(_target(), reachability=[_probe("closed", "open", 70000)])
