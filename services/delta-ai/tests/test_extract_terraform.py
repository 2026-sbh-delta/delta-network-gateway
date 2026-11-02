from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from delta_ai.contract import AssessmentRequest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("extract_terraform", ROOT / "scripts" / "extract_terraform.py")
extract_terraform = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(extract_terraform)


def _resource(type_: str, name: str, values: dict, mode: str = "managed") -> dict:
    return {"address": f"{type_}.{name}", "mode": mode, "type": type_, "name": name, "values": values}


def _show(*resources: dict, child: tuple[dict, ...] = ()) -> dict:
    root = {"resources": list(resources)}
    if child:
        root["child_modules"] = [{"address": "module.child", "resources": list(child)}]
    return {"format_version": "1.0", "values": {"root_module": root}}


SERVER = _resource("aws_instance", "server", {
    "id": "i-1", "instance_type": "t4g.nano", "ami": "ami-1", "iam_instance_profile": "", "tags": {"Name": "web"},
    "user_data": "SECRET-USER-DATA", "private_ip": "10.20.1.10"})
AMI = _resource("aws_ami", "server", {"id": "ami-1", "name": "al2023-ami-2023.1-kernel-6.1-arm64"}, mode="data")
PRIVATE = _resource("aws_subnet", "private", {"id": "subnet-1", "cidr_block": "10.20.1.0/24",
                                              "availability_zone_id": "apne2-az1", "tags": {"Name": "web-a"}})
PUBLIC = _resource("aws_subnet", "public", {"id": "subnet-2", "cidr_block": "10.20.2.0/24", "tags": {"Name": "web-pub"}})
TGW = _resource("aws_subnet", "tgw", {"id": "subnet-3", "cidr_block": "10.20.0.0/24", "tags": {"Name": "web-tgw"}})
PUBLIC_ROUTE = _resource("aws_route", "igw", {"route_table_id": "rtb-pub", "gateway_id": "igw-1"})
PRIVATE_ROUTE = _resource("aws_route", "tgw", {"route_table_id": "rtb-pri", "transit_gateway_id": "tgw-1", "gateway_id": ""})
ASSOC_PUB = _resource("aws_route_table_association", "pub", {"subnet_id": "subnet-2", "route_table_id": "rtb-pub"})
ASSOC_PRI = _resource("aws_route_table_association", "pri", {"subnet_id": "subnet-1", "route_table_id": "rtb-pri"})


def test_server_values_come_from_terraform_and_only_allowed_fields_are_copied():
    mapping = extract_terraform.extract_mapping(_show(SERVER, AMI))

    assert mapping["servers"] == [{"name": "web", "instance_type": "t4g.nano", "os": "amazon_linux",
                                   "instance_profile": False, "origin": "terraform"}]
    assert "SECRET-USER-DATA" not in json.dumps(mapping)


def test_subnet_tier_follows_the_internet_gateway_route():
    mapping = extract_terraform.extract_mapping(
        _show(PRIVATE, PUBLIC, PUBLIC_ROUTE, PRIVATE_ROUTE, ASSOC_PUB, ASSOC_PRI))

    tiers = {subnet["name"]: subnet["tier"] for subnet in mapping["subnets"]}
    assert tiers == {"web-a": "private", "web-pub": "public"}


def test_subnet_without_a_route_table_association_has_unknown_tier():
    mapping = extract_terraform.extract_mapping(_show(PRIVATE))

    assert mapping["subnets"][0]["tier"] is None


def test_child_modules_are_read_and_subnets_can_be_excluded():
    mapping = extract_terraform.extract_mapping(_show(SERVER, child=(PRIVATE, TGW)), exclude_subnets=("-tgw",))

    assert [subnet["name"] for subnet in mapping["subnets"]] == ["web-a"]
    assert len(mapping["servers"]) == 1


def test_guardduty_outside_terraform_is_unknown_not_off():
    assert extract_terraform.extract_mapping(_show(SERVER))["security"] == {"origin": "terraform"}


def test_guardduty_detector_and_features_are_read():
    detector = _resource("aws_guardduty_detector", "main", {"id": "d-1", "enable": True})
    on = _resource("aws_guardduty_detector_feature", "s3", {"name": "S3_DATA_EVENTS", "status": "ENABLED"})
    off = _resource("aws_guardduty_detector_feature", "eks", {"name": "EKS_AUDIT_LOGS", "status": "DISABLED"})

    security = extract_terraform.extract_mapping(_show(detector, on, off))["security"]

    assert security["guardduty_enabled"] is True
    assert security["guardduty_plans"] == ["S3_DATA_EVENTS"]


def test_output_is_a_valid_request():
    request = extract_terraform.build_request(extract_terraform.extract_mapping(_show(SERVER, AMI, PRIVATE)), "web")

    assert AssessmentRequest.model_validate(request).kind == "migration_mapping"
