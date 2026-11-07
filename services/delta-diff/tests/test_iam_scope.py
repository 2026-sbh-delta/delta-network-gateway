from copy import deepcopy
import json

import pytest
from delta_ai.contract import AssessmentRequest
from delta_ai.store import RuleStore
from delta_diff.contract import DiffRequest
from delta_diff.diff import DEMO_STATE, run_diff
from delta_diff.sources import StateAllowlist, StateSnapshot
from delta_diff.terraform import build_request, extract_mapping


def resource(type_, **values):
    return {"type": type_, "values": values, "mode": "managed"}


def statement(action="s3:GetObject", **changes):
    return {"Effect": "Allow", "Action": action, "Resource": "arn:aws:s3:::app/releases/*", **changes}


def iam_resources(*statements):
    return [
        resource("aws_instance", id="i-1", iam_instance_profile="app-profile", tags={"Name": "app"}),
        resource("aws_iam_instance_profile", name="app-profile", role="app-role"),
        resource("aws_iam_role", name="app-role"),
        resource("aws_iam_role_policy", role="app-role", policy=json.dumps({"Statement": list(statements)})),
    ]


def mapping(resources):
    return extract_mapping({"values": {"root_module": {"resources": resources}}})


def test_collects_only_this_profiles_actions_and_never_policy_contents():
    resources = iam_resources(statement(["s3:GetObject", "secretsmanager:GetSecretValue"]),
                              statement(["logs:PutLogEvents", "logs:DescribeLogStreams", "logs:CreateLogStream"]))
    resources.append(resource("aws_iam_role_policy", role="unrelated", policy=json.dumps({"Statement": [statement("iam:*")]})))
    resources[0]["values"]["user_data"] = "SECRET-USER-DATA"
    result = mapping(resources)
    assert result["servers"][0]["permissions"] == ["logs:CreateLogStream", "logs:DescribeLogStreams", "logs:PutLogEvents",
                                                  "s3:GetObject", "secretsmanager:GetSecretValue"]
    assert "SECRET" not in json.dumps(result) and "arn:aws" not in json.dumps(result)
    AssessmentRequest.model_validate(build_request(result, "app"))


@pytest.mark.parametrize("change", [
    {"Effect": "Deny"}, {"Condition": {"StringEquals": {"aws:PrincipalTag/team": "SECRET"}}},
    {"NotAction": "s3:DeleteObject"}, {"NotResource": "arn:aws:s3:::other/*"}, {"Action": [123]},
    {"Resource": []},
])
def test_cannot_turn_restricted_or_invalid_policy_into_partial_allow(change):
    assert mapping(iam_resources(statement(), statement(**change)))["servers"][0]["permissions"] is None


def test_missing_policy_is_unknown_but_a_known_empty_document_is_empty():
    resources = iam_resources()
    assert mapping(resources)["servers"][0]["permissions"] == []
    assert mapping(resources[:-1])["servers"][0]["permissions"] is None


def test_missing_profile_role_or_unresolved_managed_attachment_stays_unknown():
    resources = iam_resources(statement())
    for missing in (1, 2):
        assert mapping(resources[:missing] + resources[missing + 1:])["servers"][0]["permissions"] is None
    resources.append(resource("aws_iam_role_policy_attachment", role="app-role", policy_arn="arn:aws:iam::aws:policy/ReadOnlyAccess"))
    assert mapping(resources)["servers"][0]["permissions"] is None


def test_collects_resolvable_managed_policies_and_inline_role_documents():
    resources = iam_resources(statement())
    resources[2]["values"]["inline_policy"] = [{"policy": {"Statement": statement("logs:PutLogEvents")}}]
    arn = "arn:aws:iam::123456789012:policy/app"
    resources.extend([resource("aws_iam_role_policy_attachment", role="app-role", policy_arn=arn),
                      resource("aws_iam_policy", arn=arn, policy={"Statement": statement("secretsmanager:GetSecretValue")})])
    assert mapping(resources)["servers"][0]["permissions"] == ["logs:PutLogEvents", "s3:GetObject", "secretsmanager:GetSecretValue"]
    resources[-1]["values"]["arn"] = "arn:aws:iam::999999999999:policy/app"
    assert mapping(resources)["servers"][0]["permissions"] is None


def test_boundary_and_wrong_account_profile_cannot_be_flattened():
    resources = iam_resources(statement())
    resources[2]["values"]["permissions_boundary"] = "arn:aws:iam::123456789012:policy/boundary"
    assert mapping(resources)["servers"][0]["permissions"] is None
    resources[2]["values"].pop("permissions_boundary")
    resources[0]["values"]["iam_instance_profile"] = "arn:aws:iam::999999999999:instance-profile/app-profile"
    resources[1]["values"]["arn"] = "arn:aws:iam::123456789012:instance-profile/app-profile"
    assert mapping(resources)["servers"][0]["permissions"] is None


def test_wildcard_is_preserved_for_gap_review_not_expanded_to_known_permissions():
    assert mapping(iam_resources(statement("s3:*")))["servers"][0]["permissions"] == ["s3:*"]


class Reader:
    def __init__(self, lambda_present=False):
        rows = iam_resources(statement("s3:GetObject")) + [
            resource("aws_guardduty_detector", enable=True),
            *[resource("aws_guardduty_detector_feature", name=n, status="ENABLED")
              for n in ("LAMBDA_NETWORK_LOGS", "RDS_LOGIN_EVENTS", "S3_DATA_EVENTS")],
        ]
        if lambda_present:
            rows.append(resource("aws_lambda_function", function_name="app-lambda"))
        self.state = {"version": 4, "serial": 21, "resources": [
            {"type": r["type"], "mode": "managed", "instances": [{"attributes": r["values"]}]} for r in rows]}

    def read(self, _):
        return StateSnapshot(state=deepcopy(self.state), version_id="v1")


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,service,has_lambda,excluded", [
    (True, "delta-sample-app", False, True),
    (False, "delta-sample-app", False, False),
    (True, "another-app", False, False),
    (True, "delta-sample-app", True, False),
])
async def test_only_scoped_lambda_plan_is_removed_rds_s3_remain(tmp_path, monkeypatch, enabled, service, has_lambda, excluded):
    monkeypatch.setenv("DELTA_DIFF_DEMO_WORKLOAD_SCOPE", "1" if enabled else "0")
    request = DiffRequest.model_validate({"diff_id": "audit", "service_id": service,
        "source": {"provider": "aws", "state": vars(DEMO_STATE)}, "target": {"provider": "gcp", "region": "asia-northeast3"}})
    result = await run_diff(request, reader=Reader(has_lambda), allowlist=StateAllowlist(((DEMO_STATE.bucket, DEMO_STATE.key),)),
                            llm=None, store=RuleStore(tmp_path / "rules.json"))
    plans = result.mapping_input.security.guardduty_plans
    assert ("LAMBDA_NETWORK_LOGS" not in plans) is excluded
    assert {"RDS_LOGIN_EVENTS", "S3_DATA_EVENTS"} <= set(plans)
    assert result.source.excluded_capabilities == (("LAMBDA_NETWORK_LOGS",) if excluded else ())
    gaps = {r.source for r in result.assessment.mapping.rows if r.grade == "gap"}
    assert {"RDS_LOGIN_EVENTS", "S3_DATA_EVENTS"} <= gaps
    assert ("LAMBDA_NETWORK_LOGS" not in gaps) is excluded
    assert not any(r.resource == "identity" and r.grade == "unknown" for r in result.assessment.mapping.rows)
