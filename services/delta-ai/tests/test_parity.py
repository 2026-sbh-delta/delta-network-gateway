from __future__ import annotations

import copy
from typing import Any

from delta_ai.contract import AssessmentRequest
from delta_ai.parity import check_parity
from delta_ai.service import assess
from delta_ai.store import RuleStore


def _run_checks(raw: dict[str, Any]) -> Any:
    req = AssessmentRequest.model_validate(raw)
    return check_parity(req.source_env, req.target_env, req.evidence.attack_results)


def _rule_ids(findings: Any, *, status: str | None = None) -> set[str]:
    return {f.rule_id for f in findings if status is None or f.status == status}


async def test_missing_waf_on_target_returns_fix_with_cloud_armor_rules(parity_waf_missing, tmp_path):
    result = await assess(AssessmentRequest.model_validate(parity_waf_missing), llm=None,
                          store=RuleStore(tmp_path / "rules.json"))

    assert result.verdict == "fix"
    assert {"PAR-WAF-001", "PAR-WAF-002"} <= _rule_ids(result.findings, status="fail")
    expressions = {rule.expression for rule in result.remediation.waf_rules}
    assert "evaluatePreconfiguredWaf('sqli-v422-stable', {'sensitivity': 1})" in expressions
    assert "evaluatePreconfiguredWaf('xss-v422-stable', {'sensitivity': 1})" in expressions
    assert all(rule.action == "deny(403)" for rule in result.remediation.waf_rules)
    assert {"op": "replace", "path": "/waf/enabled", "value": True} in [
        op.model_dump() for op in result.remediation.spec_ops]


async def test_target_with_equivalent_controls_and_blocked_attack_passes(parity_ok, tmp_path):
    result = await assess(AssessmentRequest.model_validate(parity_ok), llm=None,
                          store=RuleStore(tmp_path / "rules.json"))

    assert result.verdict == "pass"
    assert {c.state for c in result.checks} == {"green"}
    assert result.remediation.waf_rules == ()


def test_unknown_target_field_is_unknown_not_pass(parity_ok):
    raw = copy.deepcopy(parity_ok)
    raw["target_env"]["logging"]["central"] = None

    outcome = _run_checks(raw)

    assert "PAR-LOG-001" in _rule_ids(outcome.findings, status="unknown")


def test_alert_only_attack_is_not_treated_as_blocked(parity_ok):
    raw = copy.deepcopy(parity_ok)
    raw["evidence"]["attack_results"][0]["observed"] = "alert_only"

    outcome = _run_checks(raw)

    assert "PAR-WAF-002" in _rule_ids(outcome.findings, status="unknown")


def test_missing_attack_evidence_is_unknown_when_waf_is_on(parity_ok):
    raw = copy.deepcopy(parity_ok)
    raw["evidence"]["attack_results"] = []

    outcome = _run_checks(raw)

    assert "PAR-WAF-002" in _rule_ids(outcome.findings, status="unknown")


def test_attack_through_enabled_waf_requires_human_tuning(parity_ok):
    raw = copy.deepcopy(parity_ok)
    raw["evidence"]["attack_results"][0]["observed"] = "allowed"

    outcome = _run_checks(raw)

    finding = next(f for f in outcome.findings if f.rule_id == "PAR-WAF-002")
    assert finding.status == "fail"
    assert finding.autofix == "forbidden"


def test_attack_result_for_other_environment_is_ignored(parity_ok):
    raw = copy.deepcopy(parity_ok)
    raw["evidence"]["attack_results"][0]["environment"] = "aws"

    outcome = _run_checks(raw)

    assert "PAR-WAF-002" in _rule_ids(outcome.findings, status="unknown")


def test_unmapped_aws_rule_group_is_reported_as_gap(parity_ok):
    raw = copy.deepcopy(parity_ok)
    raw["source_env"]["waf"]["rule_groups"].append("AWSManagedRulesBotControlRuleSet")

    outcome = _run_checks(raw)

    gap = next(f for f in outcome.findings if f.location.get("rule_group") == "AWSManagedRulesBotControlRuleSet")
    assert gap.status == "unknown"
    assert gap.severity == "medium"


def test_partial_mapping_is_low_note_and_keeps_check_green(parity_ok):
    outcome = _run_checks(parity_ok)

    note = next(f for f in outcome.findings if f.location.get("rule_group") == "AWSManagedRulesCommonRuleSet")
    assert note.severity == "low"
    assert next(c for c in outcome.checks if c.rule_id == "PAR-WAF-001").state == "green"


def test_waf_in_count_mode_on_target_fails(parity_ok):
    raw = copy.deepcopy(parity_ok)
    raw["target_env"]["waf"]["mode"] = "count"

    outcome = _run_checks(raw)

    finding = next(f for f in outcome.findings if f.location.get("field") == "/waf/mode")
    assert finding.status == "fail"
    assert {"op": "replace", "path": "/waf/mode", "value": "block"} in [op.model_dump() for op in finding.ops]


def test_aws_identity_carried_to_gcp_fails_and_forbids_autofix(parity_ok):
    raw = copy.deepcopy(parity_ok)
    raw["target_env"]["identity"]["mechanism"] = "irsa"

    outcome = _run_checks(raw)

    finding = next(f for f in outcome.findings if f.rule_id == "PAR-IAM-001")
    assert finding.status == "fail"
    assert finding.autofix == "forbidden"


def test_source_cloud_api_dependency_without_cross_cloud_access_fails(parity_ok):
    raw = copy.deepcopy(parity_ok)
    raw["source_env"]["identity"]["cloud_apis"] = ["s3"]

    outcome = _run_checks(raw)

    finding = next(f for f in outcome.findings if f.location.get("cloud_api") == "s3")
    assert finding.rule_id == "PAR-IAM-001"
    assert finding.autofix == "forbidden"


def test_source_cloud_api_dependency_with_confirmed_cross_cloud_access_is_ok(parity_ok):
    raw = copy.deepcopy(parity_ok)
    raw["source_env"]["identity"]["cloud_apis"] = ["s3"]
    raw["target_env"]["identity"]["cross_cloud_access"] = True

    outcome = _run_checks(raw)

    assert "PAR-IAM-001" not in _rule_ids(outcome.findings)


def test_plaintext_secret_fails_without_echoing_name_value_pairs(parity_ok):
    raw = copy.deepcopy(parity_ok)
    raw["target_env"]["secrets"]["plaintext_env"] = ["DB_PASSWORD"]

    outcome = _run_checks(raw)

    finding = next(f for f in outcome.findings if f.rule_id == "PAR-SEC-001")
    assert finding.autofix == "forbidden"
    assert "DB_PASSWORD" in finding.evidence


def test_target_without_central_logging_gets_config_fix(parity_ok):
    raw = copy.deepcopy(parity_ok)
    raw["target_env"]["logging"]["central"] = False

    outcome = _run_checks(raw)

    finding = next(f for f in outcome.findings if f.rule_id == "PAR-LOG-001")
    assert finding.autofix == "allowed"


def test_target_without_tls_gets_config_fix(parity_ok):
    raw = copy.deepcopy(parity_ok)
    raw["target_env"]["ingress"]["tls"] = False

    outcome = _run_checks(raw)

    assert "PAR-ING-001" in _rule_ids(outcome.findings, status="fail")


def test_target_outside_inspection_path_forbids_autofix(parity_ok):
    raw = copy.deepcopy(parity_ok)
    raw["target_env"]["network"]["inspected"] = False

    outcome = _run_checks(raw)

    finding = next(f for f in outcome.findings if f.rule_id == "PAR-NET-001")
    assert finding.autofix == "forbidden"


def test_gateway_swap_to_haproxy_does_not_change_the_verdict_logic(parity_ok):
    raw = copy.deepcopy(parity_ok)
    for env in ("source_env", "target_env"):
        raw[env]["network"]["gateway"] = "haproxy"

    outcome = _run_checks(raw)

    assert "PAR-NET-001" not in _rule_ids(outcome.findings)


def test_waf_fix_for_non_gcp_target_is_forbidden(parity_waf_missing):
    raw = copy.deepcopy(parity_waf_missing)
    raw["target_env"]["provider"] = "onprem"
    raw["target_env"]["identity"]["mechanism"] = None
    raw["target_env"]["secrets"]["source"] = "vault"

    outcome = _run_checks(raw)

    finding = next(f for f in outcome.findings if f.rule_id == "PAR-WAF-001")
    assert finding.autofix == "forbidden"
    assert outcome.findings


async def test_applying_returned_ops_closes_the_waf_gap(parity_waf_missing, tmp_path):
    result = await assess(AssessmentRequest.model_validate(parity_waf_missing), llm=None,
                          store=RuleStore(tmp_path / "rules.json"))
    raw = copy.deepcopy(parity_waf_missing)
    for op in result.remediation.spec_ops:
        section, field = op.path.strip("/").split("/")
        raw["target_env"][section][field] = op.value
    raw["evidence"]["attack_results"][0].update(observed="blocked", http_status=403)

    after = await assess(AssessmentRequest.model_validate(raw), llm=None, store=RuleStore(tmp_path / "rules2.json"))

    assert after.verdict == "pass"


def _gcp_to_aws(raw: dict[str, Any]) -> dict[str, Any]:
    """같은 서비스를 GCP 에서 AWS 로 옮기는 요청으로 뒤집는다. 출발 Cloud Armor, 대상은 WAF 없음."""
    flipped = copy.deepcopy(raw)
    flipped["source_env"], flipped["target_env"] = flipped["target_env"], flipped["source_env"]
    flipped["source_env"]["waf"] = {"enabled": True, "product": "cloud_armor", "mode": "block",
                                    "rule_groups": ["sqli-v422-stable", "xss-v422-stable", "lfi-v422-stable"]}
    flipped["target_env"]["waf"] = {"enabled": False}
    flipped["target_env"]["identity"]["mechanism"] = "instance_profile"
    flipped["evidence"]["attack_results"][0]["environment"] = "aws"
    return flipped


async def test_gcp_to_aws_missing_waf_returns_aws_managed_groups(parity_waf_missing, tmp_path):
    raw = _gcp_to_aws(parity_waf_missing)

    result = await assess(AssessmentRequest.model_validate(raw), llm=None, store=RuleStore(tmp_path / "rules.json"))

    assert result.verdict == "fix"
    ops = {op.path: op.value for op in result.remediation.spec_ops}
    assert ops["/waf/product"] == "aws_waf"
    assert ops["/waf/rule_groups"] == ["AWSManagedRulesCommonRuleSet", "AWSManagedRulesSQLiRuleSet"]
    rules = {rule.capability: rule for rule in result.remediation.waf_rules}
    assert set(rules) == {"sqli", "xss", "lfi"}  # 범주마다 한 줄이라 파이프라인이 범주별로 확인한다
    assert rules["sqli"].expression == "AWS/AWSManagedRulesSQLiRuleSet"
    assert all(rule.action == "managed_group" for rule in rules.values())


async def test_gcp_to_aws_applying_ops_closes_the_waf_gap(parity_waf_missing, tmp_path):
    raw = _gcp_to_aws(parity_waf_missing)
    result = await assess(AssessmentRequest.model_validate(raw), llm=None, store=RuleStore(tmp_path / "rules.json"))
    for op in result.remediation.spec_ops:
        section, field = op.path.strip("/").split("/")
        raw["target_env"][section][field] = op.value
    raw["evidence"]["attack_results"][0].update(observed="blocked", http_status=403)

    after = await assess(AssessmentRequest.model_validate(raw), llm=None, store=RuleStore(tmp_path / "rules2.json"))

    assert after.verdict == "pass"


def test_gcp_to_aws_target_missing_one_category_gets_only_that_group(parity_waf_missing):
    raw = _gcp_to_aws(parity_waf_missing)
    raw["target_env"]["waf"] = {"enabled": True, "product": "aws_waf", "mode": "block",
                                "rule_groups": ["AWSManagedRulesCommonRuleSet"]}

    outcome = _run_checks(raw)

    finding = next(f for f in outcome.findings if f.rule_id == "PAR-WAF-001" and f.status == "fail")
    assert "sqli" in finding.evidence and "xss" not in finding.evidence
    assert {r.capability for r in finding.waf_rules} == {"sqli"}


def test_cloud_armor_rule_without_an_aws_group_is_reported(parity_waf_missing):
    raw = _gcp_to_aws(parity_waf_missing)
    raw["source_env"]["waf"]["rule_groups"].append("methodenforcement-v33-stable")

    outcome = _run_checks(raw)

    assert any(f.location.get("rule_group") == "methodenforcement-v33-stable" for f in outcome.findings)


def test_vm_identity_is_native_on_each_cloud(parity_waf_missing):
    raw = _gcp_to_aws(parity_waf_missing)

    outcome = _run_checks(raw)

    assert "PAR-IAM-001" not in _rule_ids(outcome.findings)
