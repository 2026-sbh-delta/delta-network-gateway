import json
import sys

import pytest

from release_support import load


@pytest.fixture
def deployment(tmp_path, monkeypatch, data, config):
    sys.modules.pop("mapped_terraform", None)
    module = load("deploy")
    selected = config["connections"]["gcp"]
    data["database"]["mode"] = "existing"
    data["terraform_mapping"] = {
        "profile_id": "e2-small", "mapped_profile_id": "e2-micro", "subnet_name": "app",
        "subnet_cidr": "10.32.2.0/24", "state_serial": 21, "diff_request_sha256": "e" * 64,
    }
    artifact = tmp_path / "mapped"
    monkeypatch.setenv("DELTA_MAPPED_DIR", str(artifact))
    calls = []
    plan = {"complete": True, "errored": False, "resource_changes": [
        {"address": "google_compute_instance.app", "change": {"actions": ["create"]}},
    ]}

    def terraform(args, **kwargs):
        calls.append(args)
        if args[1] == "plan":
            (artifact / "deployment.tfplan").write_bytes(b"saved plan")
        elif args[1] == "show":
            return json.dumps(plan)
        elif args[1] == "output":
            return json.dumps({"private_ip": {"value": "10.32.2.5"}})

    monkeypatch.setattr(module, "run", terraform)
    return module, data, selected, config, artifact, calls, plan


def test_plan_generates_tf_json_and_only_approved_plan_is_applied(deployment, monkeypatch):
    module, data, selected, config, artifact, calls, _ = deployment
    report = module.mapped_plan(data, selected, config, planning=True)
    assert [args[1] for args in calls] == ["init", "plan", "show"]
    directory = module.Path(config["work_root"]) / data["environment_id"] / "vm"
    instance = json.loads((directory / "mapping_override.tf.json").read_text())["resource"]["google_compute_instance"]["app"]
    assert instance == {
        "machine_type": "e2-small",
        "network_interface": {"subnetwork": "${data.google_compute_subnetwork.mapped.self_link}"},
    }
    monkeypatch.setenv("DELTA_APPROVED_PLAN_SHA256", report["plan_sha256"])
    assert module.provision(data, selected, config) == {"private_ip": "10.32.2.5"}
    assert [args[1] for args in calls] == ["init", "plan", "show", "init", "apply", "output"]
    assert calls[-2][-1] == str(artifact / "deployment.tfplan")
    assert data["terraform_plan_proof"] == {key: report[key] for key in ("mapping_sha256", "plan_sha256")}


@pytest.mark.parametrize("mutation", ["plan", "approval", "config"])
def test_changed_plan_approval_or_config_stops_apply(deployment, monkeypatch, mutation):
    module, data, selected, config, artifact, calls, _ = deployment
    report = module.mapped_plan(data, selected, config, planning=True)
    monkeypatch.setenv("DELTA_APPROVED_PLAN_SHA256", report["plan_sha256"])
    if mutation == "plan":
        (artifact / "deployment.tfplan").write_bytes(b"new unapproved plan")
    elif mutation == "approval":
        monkeypatch.setenv("DELTA_APPROVED_PLAN_SHA256", "1" * 64)
    else:
        selected["terraform"]["zone"] = "asia-northeast3-b"
    with pytest.raises(module.DeployError, match="terraform_plan_mismatch"):
        module.provision(data, selected, config)
    assert not any(args[1] == "apply" for args in calls)


@pytest.mark.parametrize("actions", [["update"], ["delete", "create"], ["delete"]])
def test_existing_resource_changes_are_rejected_before_approval(deployment, actions):
    module, data, selected, config, artifact, calls, plan = deployment
    plan["resource_changes"][0]["change"]["actions"] = actions
    with pytest.raises(module.DeployError, match="plan_not_create_only"):
        module.mapped_plan(data, selected, config, planning=True)
    assert not (artifact / "binding.json").exists()
    assert not any(args[1] == "apply" for args in calls)
