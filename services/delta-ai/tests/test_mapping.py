from __future__ import annotations

import pytest
from pydantic import ValidationError

from delta_ai.contract import AssessmentRequest
from delta_ai.fake_llm import FakeLLM
from delta_ai.service import assess
from delta_ai.store import RuleStore

SERVER = {"name": "web", "instance_type": "t4g.nano", "os": "ubuntu", "multi_arch_image": True,
          "instance_profile": False}
SUBNET = {"name": "web-a", "cidr": "10.20.1.0/24", "tier": "private", "zone": "apne2-az1", "target_cidr": "10.60.1.0/24"}


def _request(**mapping: object) -> AssessmentRequest:
    return AssessmentRequest.model_validate({
        "assessment_id": "map-1", "kind": "migration_mapping", "service": {"service_id": "web"},
        "evidence": {"mapping": mapping},
    })


async def _assess(tmp_path, *, llm=None, **mapping: object):
    return await assess(_request(**mapping), llm=llm, store=RuleStore(tmp_path / "rules.sqlite3"))


def _rows(result, resource: str):
    return [row for row in result.mapping.rows if row.resource == resource]


async def test_smallest_instance_with_enough_memory_is_an_approximate_match(tmp_path):
    result = await _assess(tmp_path, servers=[SERVER])

    row = _rows(result, "compute")[0]
    assert result.verdict == "pass"
    assert row.target == "e2-micro"
    assert row.grade == "approximate"
    assert row.required_tests  # 공유 코어는 부하 시험을 확인 항목으로 낸다


@pytest.mark.parametrize(("source", "target"), [("t4g.medium", "e2-medium"), ("t3.large", "e2-standard-2"),
                                                ("t4g.xlarge", "e2-standard-4")])
async def test_instance_mapping_keeps_at_least_the_source_memory(tmp_path, source, target):
    result = await _assess(tmp_path, servers=[{**SERVER, "instance_type": source}])

    assert _rows(result, "compute")[0].target == target


async def test_unknown_instance_type_goes_to_a_person(tmp_path):
    result = await _assess(tmp_path, servers=[{**SERVER, "instance_type": "z1d.metal"}])

    assert result.verdict == "needs_human"
    assert "EVIDENCE_UNKNOWN" in result.reasons
    assert _rows(result, "compute")[0].grade == "unknown"
    assert result.findings[0].rule_id == "MAP-CMP-001"


async def test_size_is_not_reduced_without_a_person_even_with_low_utilization(tmp_path):
    result = await _assess(tmp_path, servers=[{**SERVER, "instance_type": "t4g.large", "peak_memory_percent": 10}])

    row = _rows(result, "compute")[0]
    assert row.target == "e2-standard-2"
    assert any("e2-micro" in difference for difference in row.differences)


async def test_arm_to_x86_without_a_confirmed_image_must_be_fixed(tmp_path):
    result = await _assess(tmp_path, servers=[{**SERVER, "multi_arch_image": None}])

    assert result.verdict == "fix"
    assert result.remediation.spec_ops[0].path == "/mapping/servers/0/multi_arch_image"
    assert _rows(result, "cpu_arch")[0].grade == "approximate"


async def test_x86_source_has_no_architecture_change(tmp_path):
    result = await _assess(tmp_path, servers=[{**SERVER, "instance_type": "t3.medium", "multi_arch_image": None}])

    assert result.verdict == "pass"
    assert _rows(result, "cpu_arch")[0].grade == "exact"


async def test_amazon_linux_maps_to_the_rhel_family_with_tests(tmp_path):
    result = await _assess(tmp_path, servers=[{**SERVER, "os": "amazon_linux"}])

    row = _rows(result, "os")[0]
    assert result.verdict == "pass"
    assert "RHEL" in row.target
    assert row.grade == "approximate"
    assert row.required_tests


async def test_ubuntu_keeps_the_same_os_and_unknown_os_stops(tmp_path):
    same = await _assess(tmp_path, servers=[SERVER])
    unknown = await _assess(tmp_path, servers=[{**SERVER, "os": None}])

    assert _rows(same, "os")[0].grade == "exact"
    assert unknown.verdict == "needs_human"
    assert _rows(unknown, "os")[0].grade == "unknown"


async def test_known_permissions_map_to_roles_and_others_are_gaps(tmp_path):
    known = await _assess(tmp_path, servers=[{**SERVER, "instance_profile": True, "permissions": ["s3:GetObject"]}])
    other = await _assess(tmp_path, servers=[{**SERVER, "instance_profile": True, "permissions": ["s3:*"]}])

    assert known.verdict == "pass"
    assert "roles/storage.objectViewer" in _rows(known, "identity")[0].target
    assert other.verdict == "needs_human"
    assert _rows(other, "identity")[0].grade == "gap"


async def test_instance_profile_without_permission_list_is_unknown(tmp_path):
    result = await _assess(tmp_path, servers=[{**SERVER, "instance_profile": True}])

    assert result.verdict == "needs_human"
    assert _rows(result, "identity")[0].grade == "unknown"


async def test_subnet_is_approximate_and_explains_the_public_private_difference(tmp_path):
    result = await _assess(tmp_path, subnets=[SUBNET, {**SUBNET, "name": "web-pub", "cidr": "10.20.2.0/24", "tier": "public",
                                                       "target_cidr": "10.60.2.0/24"}])

    private, public = _rows(result, "subnet")
    assert result.verdict == "pass"
    assert private.grade == public.grade == "approximate"
    assert "외부 IP 없음" in private.target
    assert "외부 IP" in public.target
    assert result.mapping.network_mode == "create"


async def test_target_cidr_overlapping_the_onprem_network_must_be_fixed(tmp_path):
    result = await _assess(tmp_path, subnets=[{**SUBNET, "target_cidr": "10.40.0.0/25"}])

    assert result.verdict == "fix"
    assert result.findings[0].rule_id == "MAP-NET-002"
    op = result.remediation.spec_ops[0]
    assert op.path == "/mapping/subnets/0/target_cidr"
    assert op.value.startswith("10.60.")


async def test_missing_values_use_defaults_and_say_so(tmp_path):
    result = await _assess(tmp_path, subnets=[{key: value for key, value in SUBNET.items() if key != "target_cidr"}])

    row = _rows(result, "subnet")[0]
    assert result.verdict == "pass"
    assert row.value_source == "default"
    assert result.mapping.region == "asia-northeast3"
    assert any("region" in assumption for assumption in result.mapping.assumptions)
    assert any("10.60." in assumption for assumption in result.mapping.assumptions)


async def test_two_default_cidrs_never_overlap(tmp_path):
    subnet = {key: value for key, value in SUBNET.items() if key != "target_cidr"}
    result = await _assess(tmp_path, subnets=[subnet, {**subnet, "name": "web-b", "cidr": "10.20.3.0/24"}])

    first, second = _rows(result, "subnet")
    assert first.target != second.target
    assert result.verdict == "pass"


async def test_reuse_mode_checks_the_existing_network_instead_of_creating(tmp_path):
    good = await _assess(tmp_path, subnets=[SUBNET], network_mode="reuse",
                         existing_subnets=[{"name": "ready", "cidr": "10.60.9.0/24", "tier": "private"}])
    missing = await _assess(tmp_path, subnets=[SUBNET], network_mode="reuse",
                            existing_subnets=[{"name": "ready", "cidr": "10.60.9.0/24", "tier": "public"}])
    small = await _assess(tmp_path, subnets=[SUBNET], network_mode="reuse",
                          existing_subnets=[{"name": "ready", "cidr": "10.60.9.0/28", "tier": "private"}])
    absent = await _assess(tmp_path, subnets=[SUBNET], network_mode="reuse")

    assert good.verdict == "pass"
    assert "ready" in _rows(good, "subnet")[0].target
    assert missing.verdict == small.verdict == absent.verdict == "needs_human"
    assert _rows(missing, "subnet")[0].grade == "gap"
    assert _rows(absent, "subnet")[0].grade == "unknown"
    assert missing.findings[0].rule_id == "MAP-NET-003"


async def test_guardduty_foundation_needs_premium_and_lists_the_log_requirement(tmp_path):
    result = await _assess(tmp_path, security={"guardduty_enabled": True}, scc_tier="premium")

    row = _rows(result, "security")[0]
    assert result.verdict == "pass"
    assert row.target == "Event Threat Detection"
    assert any("로그" in test for test in row.required_tests)


async def test_several_capabilities_become_a_split_with_every_service(tmp_path):
    result = await _assess(tmp_path, scc_tier="premium",
                           security={"guardduty_enabled": True, "guardduty_plans": ["EKS_PROTECTION", "RUNTIME_MONITORING"]})

    row = _rows(result, "security")[0]
    assert result.verdict == "pass"
    assert row.grade == "split"
    for service in ("Event Threat Detection", "Container Threat Detection", "VM Threat Detection"):
        assert service in row.target


@pytest.mark.parametrize(("tier", "grade"), [("standard", "gap"), (None, "unknown")])
async def test_security_tool_without_premium_does_not_pass(tmp_path, tier, grade):
    result = await _assess(tmp_path, security={"guardduty_enabled": True}, scc_tier=tier)

    assert result.verdict == "needs_human"
    assert _rows(result, "security")[0].grade == grade


async def test_plan_without_a_gcp_counterpart_is_a_gap_naming_what_is_lost(tmp_path):
    result = await _assess(tmp_path, scc_tier="premium",
                           security={"guardduty_enabled": True, "guardduty_plans": ["S3_PROTECTION"]})

    gaps = [row for row in _rows(result, "security") if row.grade == "gap"]
    assert result.verdict == "needs_human"
    assert gaps[0].source == "S3_PROTECTION"
    assert gaps[0].differences


async def test_a_person_can_accept_a_gap_but_it_stays_visible(tmp_path):
    result = await _assess(tmp_path, scc_tier="standard", security={"guardduty_enabled": True},
                           accepted_gaps=["log_threats"])

    row = _rows(result, "security")[0]
    assert result.verdict == "pass"
    assert row.grade == "gap"
    assert row.value_source == "operator"
    assert result.findings[0].status == "dismissed"


async def test_unmanaged_guardduty_state_and_other_tools_are_unknown(tmp_path):
    unmanaged = await _assess(tmp_path, security={})
    other = await _assess(tmp_path, security={"guardduty_enabled": False, "other_tools": ["macie"]})
    off = await _assess(tmp_path, security={"guardduty_enabled": False})

    assert unmanaged.verdict == other.verdict == "needs_human"
    assert _rows(other, "security")[0].source == "macie"
    assert off.verdict == "pass"


async def test_rows_keep_their_origin(tmp_path):
    result = await _assess(tmp_path, servers=[{**SERVER, "origin": "terraform"}])

    assert {row.value_source for row in result.mapping.rows} == {"terraform"}


async def test_every_blocking_rule_has_a_document_and_llm_cannot_change_the_verdict(tmp_path):
    mapping = {"servers": [{**SERVER, "instance_type": "z1d.metal", "os": None, "instance_profile": None}],
               "subnets": [{**SUBNET, "tier": None}], "security": {"guardduty_enabled": True}}
    without = await _assess(tmp_path, **mapping)
    with_llm = await _assess(tmp_path, llm=FakeLLM(), **mapping)

    assert all(f.has_doc for f in without.findings)
    assert "NO_EVIDENCE_DOC" not in without.reasons
    assert with_llm.verdict == without.verdict
    assert [row.model_dump() for row in with_llm.mapping.rows] == [row.model_dump() for row in without.mapping.rows]


async def test_checks_only_cover_what_was_evaluated(tmp_path):
    result = await _assess(tmp_path, servers=[SERVER])

    names = {check.rule_id for check in result.checks}
    assert "MAP-CMP-001" in names
    assert "MAP-SEC-001" not in names


def test_request_requires_something_to_map_and_a_valid_cidr():
    with pytest.raises(ValidationError):
        _request()
    with pytest.raises(ValidationError):
        _request(subnets=[{**SUBNET, "cidr": "not-a-cidr"}])
    with pytest.raises(ValidationError):
        AssessmentRequest.model_validate({"assessment_id": "m", "kind": "migration_mapping", "service": {"service_id": "w"}})
