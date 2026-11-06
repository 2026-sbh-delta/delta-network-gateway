"""배포기 흐름: 생성 전용 plan, S3 state 키, 레지스트리 복사 생략, 관측 검증, 실패 stage·code.

동규님 platform/backend/tests/test_deployments.py의 executor·onprem renderer 시험 이식 포함.
"""

import base64
import importlib
import json
import subprocess
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest

from release_support import ENVIRONMENT_ID, components, load


def show(actions):
    return json.dumps({"resource_changes": [{"change": {"actions": actions}}]})


def test_executor_never_applies_delete_or_replacement(data, config, monkeypatch):
    module = load("deploy")
    calls = []

    def command(args, **kwargs):
        calls.append(args)
        return show(["delete", "create"]) if args[1] == "show" else ""

    monkeypatch.setattr(module, "run", command)
    with pytest.raises(module.DeployError) as error:
        module.provision(data, config["connections"]["gcp"], config)
    assert (error.value.stage, error.value.code) == ("provision_vm", "plan_not_create_only")
    assert not any(c[1] == "apply" for c in calls)


def test_vm_state_is_remote_per_environment_and_role_is_delegated(data, config, monkeypatch):
    module = load("deploy")
    calls = []
    data["target"] = {"cloud": "aws", "region": "ap-northeast-2", "profile_id": "t3.small"}
    data["connection"] = {"revision": "v1", "account_id": "111122223333"}

    def command(args, **kwargs):
        calls.append((args, kwargs))
        if args[1] == "show":
            return show(["create"])
        if args[1] == "output":
            return json.dumps({"instance_id": {"value": "i-1"}, "private_ip": {"value": "10.0.0.5"}})
        return ""

    monkeypatch.setattr(module, "run", command)
    output = module.provision(data, config["connections"]["aws"], config)
    assert output == {"instance_id": "i-1", "private_ip": "10.0.0.5"}
    init = calls[0][0]
    assert f"-backend-config=key=delta-cp/release/{ENVIRONMENT_ID}/vm.tfstate" in init
    assert "-lockfile=readonly" in init and "-backend-config=use_lockfile=true" in init
    directory = Path(calls[0][1]["cwd"])
    params = json.loads((directory / "deployment.auto.tfvars.json").read_text())
    assert params["assume_role_arn"].endswith(":role/delta-prod-deploy") and params["profile_id"] == "t3.small"
    assert (directory / "main.tf").exists() and not (directory / "terraform.tfstate").exists()
    assert calls[0][1]["env"]["TF_IN_AUTOMATION"] == "1"


def test_images_already_in_target_registry_are_not_copied(data, config):
    module = load("deploy")
    assert module.target_images(data, config["connections"]["gcp"], config) == [c["image"] for c in data["components"]]
    data["target"]["cloud"] = "aws"
    with pytest.raises(module.DeployError) as error:
        module.target_images(data, config["connections"]["aws"], config)
    assert error.value.code == "image_registry_unsupported"
    data["components"] = components("111122223333.dkr.ecr.ap-northeast-2.amazonaws.com/app")
    assert module.target_images(data, config["connections"]["aws"], config)[0].startswith("111122223333.dkr.ecr.")


def test_request_rejects_image_outside_allowed_repositories(data, config, monkeypatch):
    module = load("deploy")
    monkeypatch.setenv("DELTA_RUN_ID", data["run_id"])
    encoded = base64.b64encode(json.dumps(data).encode()).decode()
    assert module.request(encoded, config)[1] is config["connections"]["gcp"]
    data["components"][1]["image"] = "evil.example/app@sha256:" + "b" * 64
    with pytest.raises(module.DeployError) as error:
        module.request(base64.b64encode(json.dumps(data).encode()).decode(), config)
    assert error.value.code == "image_not_allowed"


def test_onprem_renderer_preserves_removal_contract():
    module = load("deploy")
    data = {
        "environment_id": str(uuid4()), "service_id": str(uuid4()), "plan_hash": "a" * 64,
        "target": {"cloud": "onprem", "region": "onprem-local", "profile_id": "small", "host_id": "delta-onprem"},
        "commit_sha": "c" * 40,
        "components": [{"name": name, "image": "registry.invalid/app@sha256:" + "b" * 64,
                        "container_port": port, "healthcheck_path": "/health"}
                       for name, port in (("app", 3000), ("backend", 8080))],
    }
    objects = module.k3s_objects(data, {"namespace": "delta-apps", "image_pull_secret": "ecr-pull",
                                        "ingress_cidrs": ["10.80.1.70/32"]})
    apps = [o for o in objects if o["kind"] == "Deployment"]
    assert len(apps) == 2 and all(len(a["spec"]["template"]["spec"]["containers"]) == 1 for a in apps)
    assert sum(int(a["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]["memory"].removesuffix("Mi"))
               for a in apps) == 512
    assert all(a["spec"]["template"]["spec"]["automountServiceAccountToken"] is False for a in apps)
    assert sum(o["spec"].get("type") == "NodePort" for o in objects) == 1


def report(data, **health):
    value = {"status": "ok", "storage": "postgres", "version": data["commit_sha"], "environment": "production",
             "cloudProvider": data["target"]["cloud"], "databaseVersionNum": 180001, **health}
    return {"environment_id": data["environment_id"], "healthy": True, "health": value,
            "images": {"frontend": data["components"][0]["image"], "backend": data["components"][1]["image"]}}


def gcp_fakes(module, monkeypatch, data, *, stdout):
    seen = {}
    monkeypatch.setattr(module, "provision", lambda d, s, c: {
        "instance_id": "123", "instance_name": "delta-app-x", "private_ip": "10.10.0.5", "zone": "asia-northeast3-a"})
    server = importlib.import_module("database_server")
    monkeypatch.setattr(server, "provision", lambda d, profile, c, run, s: {
        **profile, "secret_provider": "gcp", "admin_secret_id": "projects/p/secrets/a",
        "iap_endpoint": {"address": "10.32.3.2", "port": 5432}, "connection": {"host": "db.sql.goog"}})
    gcp_vm = importlib.import_module("gcp_vm")

    @contextmanager
    def forward(output, selected, endpoint):
        seen["endpoint"] = endpoint
        yield {"health": 31001, "database": 31002}

    monkeypatch.setattr(gcp_vm, "forward", forward)

    def ssh(args, account, runner):
        seen["command"] = args[args.index("--command") + 1]
        return "log line\n" + stdout

    monkeypatch.setattr(gcp_vm, "ssh", ssh)
    database = importlib.import_module("database")

    def prepare(d, profile, *, client, connector):
        seen["store"] = type(client).__name__
        return "delta-platform/customer-db/x", {"password": "fixture-app-password"}, 180001

    monkeypatch.setattr(database, "prepare", prepare)
    monkeypatch.setattr(database, "deliver", lambda d, s, secret_id, value: {"provider": "gcp", "version": "v/1"})
    monkeypatch.setattr(module, "health", lambda url: None)
    monkeypatch.setattr(module, "database_health", lambda url: None)
    return seen


def test_gcp_vm_deploy_reports_observed_images_and_postgres18(data, config, monkeypatch):
    module = load("deploy")
    seen = gcp_fakes(module, monkeypatch, data, stdout=json.dumps(report(data)))
    environment, url, observation = module.vm(data, config["connections"]["gcp"], config)
    assert observation["database_version_num"] == 180001
    assert observation["images"] == {"frontend": data["components"][0]["image"], "backend": data["components"][1]["image"]}
    assert url == "http://10.10.0.5:8088" and environment["instances"][0]["name"] == "delta-app-x"
    assert seen["endpoint"] == {"address": "10.32.3.2", "port": 5432} and seen["store"] == "GcpSecretStore"
    # VM 명령에는 비밀 참조만 들어간다(앱 비밀번호 원문 없음). 로그는 gcplogs, 환경은 production.
    script = base64.b64decode(seen["command"].split("b64decode('")[1].split("'")[0]).decode()
    payload = json.loads(base64.b64decode(script.split("input=base64.b64decode('")[1].split("'")[0]))
    assert "fixture-app-password" not in json.dumps(payload)
    assert payload["logging"]["driver"] == "gcplogs" and payload["database_secret"]["provider"] == "gcp"
    assert payload["runtime_env"]["backend"]["APP_ENV"] == "production"
    assert payload["runtime_env"]["frontend"]["PORT"] == "3000"


@pytest.mark.parametrize("change,code", [
    (lambda r: r["images"].update(backend="asia-northeast3-docker.pkg.dev/fixture/app/backend@sha256:" + "e" * 64),
     "image_mismatch"),
    (lambda r: r["health"].update(version="f" * 40), "health_mismatch"),
    (lambda r: r["health"].update(databaseVersionNum=160004), "database_health_mismatch"),
    (lambda r: r["health"].update(storage="memory"), "database_health_mismatch"),
])
def test_observation_that_differs_from_request_fails_with_code(data, config, monkeypatch, change, code):
    module = load("deploy")
    value = report(data)
    change(value)
    gcp_fakes(module, monkeypatch, data, stdout=json.dumps(value))
    with pytest.raises(module.DeployError) as error:
        module.vm(data, config["connections"]["gcp"], config)
    assert (error.value.stage, error.value.code) == ("observe", code)


def test_non_postgres18_server_is_rejected_before_vm_runtime(data, config, monkeypatch):
    module = load("deploy")
    seen = gcp_fakes(module, monkeypatch, data, stdout="{}")
    database = importlib.import_module("database")
    monkeypatch.setattr(database, "prepare", lambda d, p, client, connector: ("s", {}, 170005))
    with pytest.raises(module.DeployError) as error:
        module.vm(data, config["connections"]["gcp"], config)
    assert (error.value.stage, error.value.code) == ("database_prepare", "postgres_version")
    assert "command" not in seen


def run_main(module, tmp_path, monkeypatch, data, config):
    path = tmp_path / "release-config.json"
    path.write_text(json.dumps(config))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DELTA_DEPLOY_CONFIG", str(path))
    monkeypatch.setenv("DELTA_RUN_ID", data["run_id"])
    monkeypatch.setenv("DELTA_DEPLOYMENT", base64.b64encode(json.dumps(data).encode()).decode())
    status = module.main()
    return status, json.loads((tmp_path / "result.json").read_text())


def test_failure_result_has_stage_and_code_without_command_output(data, config, monkeypatch, tmp_path):
    module = load("deploy")
    monkeypatch.setattr(module.subprocess, "run", lambda args, **kw: subprocess.CompletedProcess(
        args, 1, stdout="PRIVATE-STDOUT", stderr="PRIVATE-STDERR password=hunter2"))
    status, result = run_main(module, tmp_path, monkeypatch, data, config)
    assert status == 1
    assert result == {"state": "failed", "stage": "provision_vm", "code": "terraform_init_failed",
                      "run_id": data["run_id"], "environment_id": data["environment_id"]}
    assert "PRIVATE" not in json.dumps(result) and "hunter2" not in json.dumps(result)


def test_rejected_request_records_request_stage(data, config, monkeypatch, tmp_path):
    module = load("deploy")
    data["components"][0]["image"] = "evil.example/app@sha256:" + "a" * 64
    status, result = run_main(module, tmp_path, monkeypatch, data, config)
    assert status == 1 and result == {"state": "failed", "stage": "request", "code": "image_not_allowed"}


def test_successful_main_writes_verified_result(data, config, monkeypatch, tmp_path):
    module = load("deploy")
    gcp_fakes(module, monkeypatch, data, stdout=json.dumps(report(data)))
    status, result = run_main(module, tmp_path, monkeypatch, data, config)
    assert status == 0 and result["state"] == "verified"
    assert result["database"]["server_version_num"] == 180001
    assert result["observed"]["images"]["backend"] == data["components"][1]["image"]
