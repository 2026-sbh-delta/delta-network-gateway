"""정리: 동규님 platform/backend/tests/test_project_cleanup.py의 destroy plan 시험 이식 + 원격 state 경로."""

import copy
import json
from uuid import uuid4

import pytest

from release_support import load


def test_destroy_plan_refuses_other_customers_or_extra_resources():
    module = load("remove")
    data = {"service_id": str(uuid4()), "environment_id": str(uuid4()), "target": {"cloud": "aws"}}
    change = {"address": "aws_instance.app", "change": {"actions": ["delete"], "before": {"tags": {
        "ManagedBy": "delta-platform", "ServiceId": data["service_id"], "Environment": data["environment_id"]}}}}
    module.validate_changes({"resource_changes": [change]}, data)
    for kind in ("other_owner", "other_resource", "additional_disk"):
        bad = copy.deepcopy(change)
        if kind == "other_owner":
            bad["change"]["before"]["tags"]["ServiceId"] = str(uuid4())
        if kind == "other_resource":
            bad["address"] = "aws_vpc.shared"
        if kind == "additional_disk":
            bad["change"]["before"]["ebs_block_device"] = [{"volume_id": "external"}]
        with pytest.raises(ValueError):
            module.validate_changes({"resource_changes": [bad]}, data)


def test_remove_uses_same_remote_state_and_checks_provider_when_state_is_empty(data, config, monkeypatch):
    module = load("remove")
    calls, checked = [], []

    def command(args, **kwargs):
        calls.append(args)
        return ""

    monkeypatch.setattr(module, "run", command)
    monkeypatch.setattr(module, "absent", lambda d, s: checked.append(d["environment_id"]))
    module.vm(data, config["connections"]["gcp"], config)
    assert f"-backend-config=key=delta-cp/release/{data['environment_id']}/vm.tfstate" in calls[0]
    assert calls[1] == ["terraform", "state", "list"] and checked == [data["environment_id"]]
    assert not any("apply" in c for c in calls)


def test_remove_destroys_only_after_owner_check(data, config, monkeypatch):
    module = load("remove")
    calls, listed = [], iter(["google_compute_instance.app\n", ""])
    before = {"labels": {"managed-by": "delta-platform", "service-id": data["service_id"],
                         "environment": data["environment_id"]}}

    def command(args, **kwargs):
        calls.append(args)
        if args[1:3] == ["state", "list"]:
            return next(listed)
        if args[1] == "show":
            return json.dumps({"resource_changes": [{"address": "google_compute_instance.app",
                                                     "change": {"actions": ["delete"], "before": before}}]})
        return ""

    monkeypatch.setattr(module, "run", command)
    module.vm(data, config["connections"]["gcp"], config)
    assert ["terraform", "apply", "-input=false", "removal.tfplan"] in calls
