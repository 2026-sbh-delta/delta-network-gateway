from __future__ import annotations

import pytest
from pydantic import ValidationError

from delta_ai.contract import AssessmentRequest
from delta_ai.fake_llm import FakeLLM
from delta_ai.mapping_catalog import AWS_HOURLY_USD, AWS_INSTANCES, GCP_HOURLY_USD, GCP_INSTANCES
from delta_ai.service import assess
from delta_ai.store import RuleStore

DEFAULT_SOURCE_REGION = "ap-northeast-2"
DEFAULT_TARGET_REGION = "asia-northeast3"
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
                           security={"guardduty_enabled": True, "guardduty_plans": ["EKS_RUNTIME_MONITORING", "RUNTIME_MONITORING"]})

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


async def test_each_capability_lost_without_premium_is_its_own_distinguishable_row(tmp_path):
    result = await _assess(tmp_path, scc_tier="standard",
                           security={"guardduty_enabled": True, "guardduty_plans": ["RUNTIME_MONITORING"]})

    gaps = _rows(result, "security")
    sources = [row.source for row in gaps]
    assert [row.grade for row in gaps] == ["gap"] * 3
    assert len(set(sources)) == 3
    for source, service in zip(sources, ("Container Threat Detection", "Event Threat Detection",
                                         "VM Threat Detection"), strict=True):
        row = next(r for r in gaps if r.source == source)
        assert service in row.differences[0]
    assert len({f.evidence for f in result.findings if f.rule_id == "MAP-SEC-001"}) == 3


async def test_plan_without_a_gcp_counterpart_is_a_gap_naming_what_is_lost(tmp_path):
    result = await _assess(tmp_path, scc_tier="premium",
                           security={"guardduty_enabled": True, "guardduty_plans": ["RDS_LOGIN_EVENTS"]})

    gaps = [row for row in _rows(result, "security") if row.grade == "gap"]
    assert result.verdict == "needs_human"
    assert gaps[0].source == "RDS_LOGIN_EVENTS"
    assert "accepted_gaps" in gaps[0].differences[0]


# GuardDuty API·Terraform aws_guardduty_detector_feature 가 쓰는 실제 기능 이름이다
@pytest.mark.parametrize(("feature", "service"), [("EBS_MALWARE_PROTECTION", "VM Threat Detection"),
                                                  ("EKS_RUNTIME_MONITORING", "Container Threat Detection"),
                                                  ("EKS_AUDIT_LOGS", "Event Threat Detection")])
async def test_real_guardduty_feature_names_map_to_a_scc_service(tmp_path, feature, service):
    result = await _assess(tmp_path, scc_tier="premium",
                           security={"guardduty_enabled": True, "guardduty_plans": [feature]})

    rows = _rows(result, "security")
    assert result.verdict == "pass"
    assert [row.grade for row in rows if row.grade == "gap"] == []
    assert service in rows[0].target


async def test_live_aws_features_lose_only_what_scc_cannot_cover(tmp_path):
    features = ["S3_DATA_EVENTS", "EKS_AUDIT_LOGS", "EBS_MALWARE_PROTECTION", "RDS_LOGIN_EVENTS", "LAMBDA_NETWORK_LOGS"]
    result = await _assess(tmp_path, scc_tier="premium", security={"guardduty_enabled": True, "guardduty_plans": features})

    gaps = {row.source for row in _rows(result, "security") if row.grade == "gap"}
    assert gaps == {"S3_DATA_EVENTS", "RDS_LOGIN_EVENTS", "LAMBDA_NETWORK_LOGS"}


async def test_a_feature_for_an_unused_service_can_be_accepted_and_stays_visible(tmp_path):
    result = await _assess(tmp_path, scc_tier="premium",
                           security={"guardduty_enabled": True, "guardduty_plans": ["RDS_LOGIN_EVENTS"]},
                           accepted_gaps=["RDS_LOGIN_EVENTS"])

    assert result.verdict == "pass"
    assert [row.value_source for row in _rows(result, "security") if row.grade == "gap"] == ["operator"]


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


@pytest.mark.parametrize(("mapping", "message"), [
    ({"servers": [{**SERVER, "instance_type": "e2-micro"}]}, "AWS 인스턴스 타입이 아니다"),  # GCP 서버를 출발로 넣음
    ({"servers": [SERVER], "region": "ap-northeast-2"}, "GCP 리전이 아니다"),  # 대상 리전이 AWS
    ({"servers": [SERVER], "source_region": "asia-northeast3"}, "AWS 리전이 아니다"),  # 출발 리전이 GCP
    ({"servers": [SERVER], "source_provider": "gcp"}, "gcp → gcp"),
    ({"servers": [SERVER], "target_provider": "aws"}, "aws → aws"),
    ({"servers": [SERVER], "source_provider": "onprem"}, "onprem → gcp"),
])
def test_only_aws_gcp_directions_with_matching_values_are_accepted(mapping, message):
    with pytest.raises(ValidationError, match=message):
        _request(**mapping)


def test_aws_to_gcp_direction_is_stated_in_the_input():
    mapping = _request(servers=[SERVER]).evidence.mapping

    assert (mapping.source_provider, mapping.target_provider) == ("aws", "gcp")


def _server(name: str, instance_type: str) -> dict[str, object]:
    return {**SERVER, "name": name, "instance_type": instance_type}


async def test_cost_compares_the_mapped_servers_for_the_same_hours(tmp_path):
    result = await _assess(tmp_path, servers=[SERVER])

    cost = result.mapping.cost
    line = cost.lines[0]
    assert (cost.hours_per_month, cost.currency) == (730, "USD")
    assert (line.source_type, line.target_type) == ("t4g.nano", "e2-micro")
    assert (line.source_monthly, line.target_monthly, line.difference) == (3.80, 7.84, 4.04)


async def test_cost_totals_add_up_from_the_lines_shown(tmp_path):
    result = await _assess(tmp_path, servers=[_server("a", "t4g.nano"), _server("b", "t3.large")])

    cost = result.mapping.cost
    assert cost.source_total == round(sum(line.source_monthly for line in cost.lines), 2)
    assert cost.target_total == round(sum(line.target_monthly for line in cost.lines), 2)
    assert cost.difference_total == round(cost.target_total - cost.source_total, 2)


async def test_cost_says_when_the_target_is_larger_than_the_source(tmp_path):
    larger = await _assess(tmp_path, servers=[_server("a", "t4g.nano")])
    same = await _assess(tmp_path, servers=[_server("a", "t4g.small")])

    assert any("메모리" in note for note in larger.mapping.cost.lines[0].notes)
    assert same.mapping.cost.lines[0].notes == ()


async def test_unpriced_server_has_no_estimate_and_no_partial_total(tmp_path):
    result = await _assess(tmp_path, servers=[_server("a", "t4g.nano"), _server("b", "z1d.metal")])

    cost = result.mapping.cost
    assert cost.lines[1].source_monthly is None
    assert cost.lines[1].target_type is None
    assert cost.lines[1].difference is None
    assert cost.source_total is None
    assert cost.difference_total is None


@pytest.mark.parametrize("regions", [{"region": "us-central1"}, {"source_region": "us-east-1"}])
async def test_region_without_a_price_is_not_guessed_from_another_region(tmp_path, regions):
    result = await _assess(tmp_path, servers=[SERVER], **regions)

    line = result.mapping.cost.lines[0]
    assert line.difference is None
    assert result.mapping.cost.difference_total is None
    assert any("단가" in note for note in line.notes)


async def test_cost_never_changes_the_verdict_or_findings(tmp_path):
    result = await _assess(tmp_path, servers=[{**SERVER, "instance_type": "t3.xlarge", "multi_arch_image": None}])

    assert result.verdict == "pass"
    assert result.findings == () or all(f.severity == "low" for f in result.findings)
    assert result.mapping.cost.difference_total < 0  # 대상이 싸도 판정에 영향이 없다


async def test_cost_states_its_basis_and_what_it_leaves_out(tmp_path):
    result = await _assess(tmp_path, servers=[SERVER])

    cost = result.mapping.cost
    assert cost.priced_on
    assert cost.source_region == "ap-northeast-2"
    assert cost.target_region == "asia-northeast3"
    for excluded in ("디스크", "OS 라이선스", "보안 도구"):
        assert any(excluded in item for item in cost.excludes)
    assert any("ap-northeast-2" in assumption for assumption in result.mapping.assumptions)


async def test_no_servers_means_no_cost_section(tmp_path):
    result = await _assess(tmp_path, subnets=[SUBNET])

    assert result.mapping.cost is None


def test_every_instance_in_the_mapping_table_has_a_price():
    assert set(AWS_HOURLY_USD[DEFAULT_SOURCE_REGION]) == set(AWS_INSTANCES)
    assert set(GCP_HOURLY_USD[DEFAULT_TARGET_REGION]) == set(GCP_INSTANCES)


# --- GCP → AWS ---------------------------------------------------------------------------------------------------

GCP_SERVER = {"name": "web", "instance_type": "e2-small", "os": "ubuntu", "service_account": False}


def _gcp(**mapping: object) -> dict[str, object]:
    return {"source_provider": "gcp", "target_provider": "aws", **mapping}


@pytest.mark.parametrize(("source", "target"), [("e2-micro", "t3.micro"), ("e2-medium", "t3.medium"),
                                                ("e2-standard-2", "t3.large"), ("e2-standard-4", "t3.xlarge")])
async def test_gcp_e2_maps_to_the_x86_t3_with_at_least_the_memory(tmp_path, source, target):
    result = await _assess(tmp_path, **_gcp(servers=[{**GCP_SERVER, "instance_type": source}]))

    compute = _rows(result, "compute")[0]
    assert compute.target == target
    assert any("CPU 크레딧" in test for test in compute.required_tests)  # T 계열 버스트는 부하 시험을 낸다
    assert _rows(result, "cpu_arch")[0].grade == "exact"  # E2 와 t3 는 둘 다 x86 이라 재빌드가 없다
    assert result.verdict == "pass"


async def test_gcp_to_aws_uses_aws_region_and_prices(tmp_path):
    result = await _assess(tmp_path, **_gcp(servers=[GCP_SERVER]))

    assert result.mapping.region == DEFAULT_SOURCE_REGION
    line = result.mapping.cost.lines[0]
    assert (line.source_type, line.target_type) == ("e2-small", "t3.small")
    assert line.source_monthly == round(GCP_HOURLY_USD[DEFAULT_TARGET_REGION]["e2-small"] * 730, 2)
    assert line.target_monthly == round(AWS_HOURLY_USD[DEFAULT_SOURCE_REGION]["t3.small"] * 730, 2)


async def test_gcp_service_account_roles_become_instance_profile_actions(tmp_path):
    server = {**GCP_SERVER, "service_account": True,
              "permissions": ["roles/storage.objectViewer", "roles/secretmanager.secretAccessor"]}

    row = _rows(await _assess(tmp_path, **_gcp(servers=[server])), "identity")[0]

    assert row.grade == "approximate"
    assert row.target == "인스턴스 프로파일: s3:GetObject, s3:ListBucket, secretsmanager:GetSecretValue"


async def test_unmapped_gcp_role_is_a_gap(tmp_path):
    server = {**GCP_SERVER, "service_account": True, "permissions": ["roles/pubsub.publisher"]}

    result = await _assess(tmp_path, **_gcp(servers=[server]))

    assert _rows(result, "identity")[0].grade == "gap"
    assert result.verdict == "needs_human"


async def test_gcp_subnet_explains_that_aws_subnets_are_zonal(tmp_path):
    subnet = {**SUBNET, "zone": None}

    row = _rows(await _assess(tmp_path, **_gcp(subnets=[subnet])), "subnet")[0]

    assert "AZ" in row.differences[0] and "NAT 게이트웨이" in row.target


async def test_container_optimized_os_maps_approximately(tmp_path):
    row = _rows(await _assess(tmp_path, **_gcp(servers=[{**GCP_SERVER, "os": "cos"}])), "os")[0]

    assert row.grade == "approximate"
    assert "Bottlerocket" in row.target


async def test_scc_services_map_to_guardduty_features_without_asking_a_tier(tmp_path):
    security = {"scc_services": ["Event Threat Detection", "Container Threat Detection", "VM Threat Detection"]}

    result = await _assess(tmp_path, **_gcp(security=security))

    row = _rows(result, "security")[0]
    assert row.target == "GuardDuty 기본 탐지, RUNTIME_MONITORING"
    assert row.grade == "split"
    assert any("에이전트" in d for d in row.differences)  # VM 탐지는 에이전트가 있어야 한다
    assert result.verdict == "pass"


async def test_log_threats_only_need_the_guardduty_foundation(tmp_path):
    row = _rows(await _assess(tmp_path, **_gcp(security={"scc_services": ["Event Threat Detection"]})), "security")[0]

    assert (row.target, row.grade) == ("GuardDuty 기본 탐지", "approximate")


async def test_scc_without_threat_detection_has_nothing_to_carry(tmp_path):
    row = _rows(await _assess(tmp_path, **_gcp(security={"scc_services": []})), "security")[0]

    assert row.grade == "exact"


async def test_unmapped_scc_service_is_a_gap_a_person_can_accept(tmp_path):
    security = {"scc_services": ["Event Threat Detection", "Security Health Analytics"]}

    blocked = await _assess(tmp_path, **_gcp(security=security))
    accepted = await _assess(tmp_path, **_gcp(security=security, accepted_gaps=["Security Health Analytics"]))

    assert blocked.verdict == "needs_human"
    assert any(r.grade == "gap" and r.source == "Security Health Analytics" for r in blocked.mapping.rows)
    assert accepted.verdict == "pass"
    assert any(r.grade == "gap" for r in accepted.mapping.rows)  # 승인해도 표에 남는다


async def test_unknown_scc_services_go_to_a_person(tmp_path):
    result = await _assess(tmp_path, **_gcp(security={}))

    assert result.verdict == "needs_human"
    assert _rows(result, "security")[0].grade == "unknown"


@pytest.mark.parametrize(("mapping", "message"), [
    (_gcp(servers=[{**GCP_SERVER, "instance_type": "t3.small"}]), "GCP 인스턴스 타입이 아니다"),
    (_gcp(servers=[GCP_SERVER], region="asia-northeast3"), "AWS 리전이 아니다"),
    (_gcp(servers=[GCP_SERVER], source_region="ap-northeast-2"), "GCP 리전이 아니다"),
    (_gcp(servers=[{**GCP_SERVER, "instance_profile": True}]), "instance_profile 는 받지 않는다"),
    (_gcp(security={"guardduty_enabled": True}), "guardduty 값은 받지 않는다"),
    (_gcp(servers=[GCP_SERVER], scc_tier="premium"), "scc_tier 는 GCP 대상에서만"),
    ({"servers": [{**SERVER, "service_account": True}]}, "service_account 는 받지 않는다"),
    ({"security": {"scc_services": []}}, "scc_services 는 받지 않는다"),
])
def test_values_from_the_other_cloud_are_rejected_per_direction(mapping, message):
    with pytest.raises(ValidationError, match=message):
        _request(**mapping)


async def test_aws_target_may_use_the_aws_server_vpc_but_gcp_target_may_not(tmp_path):
    subnet = {**SUBNET, "cidr": "10.60.1.0/24", "target_cidr": "10.20.1.0/24"}

    to_aws = await _assess(tmp_path, **_gcp(subnets=[subnet]))
    to_gcp = await _assess(tmp_path, subnets=[{**SUBNET, "target_cidr": "10.20.1.0/24"}])

    assert "MAP-NET-002" not in {f.rule_id for f in to_aws.findings}
    assert "MAP-NET-002" in {f.rule_id for f in to_gcp.findings}


async def test_default_aws_target_cidr_comes_from_the_aws_pool(tmp_path):
    result = await _assess(tmp_path, **_gcp(subnets=[{**SUBNET, "cidr": "10.60.1.0/24", "target_cidr": None}]))

    assert _rows(result, "subnet")[0].target.startswith("10.20.")
