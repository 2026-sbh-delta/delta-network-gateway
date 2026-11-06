"""vm_release: 우리 운영 요청 → 동규님 DELTA_DEPLOYMENT, 배포기 관측 → 파이프라인 결과 계약. release_deploy.main 연결."""

import base64
import copy
import json
import subprocess
import sys

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .delivery_support import canonical_hash, load_controller

CONTRACT_KEYS = {"state", "target_id", "environment", "commit", "schema", "platform", "images", "image_digests",
                 "bundle_sha256", "image_ref", "image_digest", "database_version_num", "deployment_id",
                 "config_sha256"}


@pytest.fixture
def vm_release():
    return load_controller("vm_release")


@pytest.fixture
def release_config(tmp_path, monkeypatch):
    config = {
        "state_bucket": "fixture-state-bucket", "state_region": "ap-northeast-2",
        "allowed_image_prefixes": [],
        "connections": {"gcp": {"identity": {"revision": "v1", "gcp_project": "fixture-project"},
                                "credentials_file": "/run/delta/fixture-wif.json"}},
        "databases": {"fixture-postgres18": {"revision": "v3", "modes": ["new"], "clouds": ["gcp"]}},
    }
    path = tmp_path / "release-config.json"
    path.write_text(json.dumps(config))
    monkeypatch.setenv("DELTA_RELEASE_CONFIG", str(path))
    return config


def deployed(data, *, images=None, version=180003, **changes):
    value = {
        "state": "verified", "run_id": data["run_id"], "environment_id": data["environment_id"],
        "commit_sha": data["commit_sha"],
        "observed": {"images": images or {c["name"]: c["image"] for c in data["components"]},
                     "health": {"status": "ok"}},
        "database": {"server_version_num": version},
        "environment": {"instances": [{"id": "123", "name": "delta-app-x", "zone": "asia-northeast3-a"}]},
        "service_url": "http://10.10.0.5:8088",
    }
    value.update(changes)
    return value


def fake_runner(result_for, seen):
    def runner(args, *, cwd, env, **kwargs):
        data = json.loads(base64.b64decode(env["DELTA_DEPLOYMENT"]))
        seen.update(args=args, env=env, data=data, kwargs=kwargs)
        value = result_for(data)
        if value is not None:
            with open(str(cwd) + "/result.json", "w") as stream:
                json.dump(value, stream)
        return subprocess.CompletedProcess(args, 0, "", "")
    return runner


def test_request_maps_to_deployer_format_with_target_registry_images(vm_release, release_request, release_config):
    request = copy.deepcopy(release_request)
    request["bundle"]["registry_images"] = {"gcp": dict(request["bundle"]["images"])}
    data = vm_release.deployment_request(request, release_config)
    assert data["schema_version"] == 1 and data["commit_sha"] == request["commit_sha"]
    assert [(c["name"], c["container_port"], c["healthcheck_path"]) for c in data["components"]] == [
        ("frontend", 3000, "/healthz"), ("backend", 8080, "/healthz")]
    assert {c["name"]: c["image"] for c in data["components"]} == request["bundle"]["images"]
    assert data["database"] == {"profile_id": "fixture-postgres18", "revision": "v3", "engine": "postgres", "mode": "new"}
    assert data["target"] == {"cloud": "gcp", "region": "asia-northeast3", "profile_id": "e2-small"}
    assert data["connection"] == release_config["connections"]["gcp"]["identity"]
    # 같은 대상은 같은 환경(VM·DB)을 재사용하고, 배포마다 실행 ID만 바뀐다.
    other = vm_release.deployment_request({**request, "deployment_id": "deployment-b"}, release_config)
    assert other["environment_id"] == data["environment_id"] and other["run_id"] != data["run_id"]
    pinned = {**request, "target": {**request["target"], "environment_id": "00000000-0000-4000-8000-00000000000f"}}
    assert vm_release.deployment_request(pinned, release_config)["environment_id"].endswith("0f")


def test_registry_copy_that_differs_from_approved_images_is_refused(vm_release, release_request, release_config):
    request = copy.deepcopy(release_request)
    request["bundle"]["registry_images"] = {"gcp": {k: v.replace("registry.invalid", "other.invalid")
                                                    for k, v in request["bundle"]["images"].items()}}
    with pytest.raises(ValueError):
        vm_release.deployment_request(request, release_config)


def test_verified_deployment_maps_to_pipeline_contract(vm_release, release_request, release_config):
    seen = {}
    result = vm_release.execute(release_request, runner=fake_runner(deployed, seen))
    bundle = release_request["bundle"]
    assert CONTRACT_KEYS <= set(result)
    assert result["state"] == "verified" and result["environment"] == "production"
    assert result["target_id"] == "fixture-gcp" and result["deployment_id"] == "deployment-a"
    assert result["config_sha256"] == release_request["config_sha256"]
    for key in ("commit", "schema", "platform", "images", "image_digests", "bundle_sha256", "image_ref", "image_digest"):
        assert result[key] == bundle[key], key
    assert type(result["database_version_num"]) is int and result["database_version_num"] // 10000 == 18
    # 승인 토큰·Jenkins 파라미터는 배포기 프로세스에 넘기지 않는다. GCP는 WIF 설정만 넘긴다.
    env = seen["env"]
    assert "APPROVAL" not in env and "IMAGES_JSON" not in env
    assert env["DELTA_RUN_ID"] == seen["data"]["run_id"]
    assert env["GOOGLE_APPLICATION_CREDENTIALS"] == "/run/delta/fixture-wif.json"
    assert seen["args"][-1].endswith("release/deploy.py") and seen["kwargs"]["capture_output"]


@pytest.mark.parametrize("change", [
    lambda d: deployed(d, images={"frontend": d["components"][0]["image"],
                                  "backend": "registry.invalid/app/backend@sha256:" + "e" * 64}),
    lambda d: deployed(d, version=160004),
    lambda d: deployed(d, version="180003"),
    lambda d: deployed(d, commit_sha="f" * 40),
    lambda d: deployed(d, run_id="other"),
    lambda d: {k: v for k, v in deployed(d).items() if k != "database"},
])
def test_observation_that_differs_is_not_verified(vm_release, release_request, release_config, change):
    result = vm_release.execute(release_request, runner=fake_runner(change, {}))
    assert result["state"] == "failed" and (result["stage"], result["code"]) == ("verify", "observation_mismatch")
    assert "images" not in result


def test_deployer_failure_keeps_only_stage_and_code(vm_release, release_request, release_config):
    result = vm_release.execute(release_request, runner=fake_runner(
        lambda d: {"state": "failed", "stage": "provision_vm", "code": "terraform_apply_failed", "noise": "x"}, {}))
    assert result == {"deployment_id": "deployment-a", "target_id": "fixture-gcp", "environment": "production",
                      "config_sha256": release_request["config_sha256"], "state": "failed",
                      "stage": "provision_vm", "code": "terraform_apply_failed"}
    hostile = vm_release.execute(release_request, runner=fake_runner(
        lambda d: {"state": "failed", "stage": "Password=x", "code": "secret value"}, {}))
    assert (hostile["stage"], hostile["code"]) == ("deploy", "deploy_failed")


def test_missing_result_and_timeout_are_unknown(vm_release, release_request, release_config):
    assert vm_release.execute(release_request, runner=fake_runner(lambda d: None, {}))["code"] == "deployer_no_result"

    def timeout(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 1)

    result = vm_release.execute(release_request, runner=timeout)
    assert (result["state"], result["code"]) == ("unknown", "deployer_timeout")


@pytest.mark.parametrize("change", [
    {"cloud": "onprem"}, {"environment": "staging"}, {"profile_id": "e2-micro"}, {"region": "us-central1"},
    {"db_secret_ref": "x"}, {"database": {"profile_id": "p", "mode": "copy"}},
    {"database": {"profile_id": "p", "mode": "new", "host": "db"}}, {"environment_id": "not-a-uuid"},
    {"target_id": "../x"},
])
def test_inventory_shape_is_strict(vm_release, release_request, change):
    with pytest.raises(ValueError):
        vm_release.validate_target({**release_request["target"], **change})


def sign(request, key, **changes):
    claims = {"deployment_id": request["deployment_id"], "commit_sha": request["commit_sha"],
              "bundle_sha256": request["bundle"]["bundle_sha256"], "target_id": request["target"]["target_id"],
              "environment": "production", "config_sha256": request["config_sha256"],
              "exp": 10**10, "nonce": "nonce-main", **changes}
    payload = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
    return {"claims": claims, "signature": base64.urlsafe_b64encode(key.sign(payload)).decode().rstrip("=")}


@pytest.fixture
def main_env(release_module, release_request, tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    public = tmp_path / "release-public.pem"
    public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,
                                                     serialization.PublicFormat.SubjectPublicKeyInfo))
    targets = tmp_path / "release-targets.json"
    targets.write_text(json.dumps({"fixture-gcp": release_request["target"]}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(release_module.time, "time", lambda: 10**10 - 300)
    for name, value in {"DELTA_RELEASE_TARGETS": str(targets), "DELTA_RELEASE_PUBLIC_KEY": str(public),
                        "DELTA_RELEASE_DB": str(tmp_path / "release.sqlite3"), "DELTA_RELEASE_RESULTS": str(tmp_path / "results"), "TARGET_ID": "fixture-gcp",
                        "RUN_ID": "deployment-a", "COMMIT": release_request["commit_sha"],
                        "IMAGES_JSON": json.dumps(release_request["bundle"]["images"]),
                        "BUNDLE_SHA256": release_request["bundle"]["bundle_sha256"],
                        "APPROVAL": json.dumps(sign(release_request, key))}.items():
        monkeypatch.setenv(name, value)
    return tmp_path


def test_main_writes_verified_contract(release_module, release_request, main_env, monkeypatch):
    bundle = release_request["bundle"]
    calls = []

    def execute(request):
        calls.append(request)
        return {**{k: bundle[k] for k in ("commit", "schema", "platform", "images", "image_digests", "bundle_sha256",
                                          "image_ref", "image_digest")},
                "state": "verified", "target_id": "fixture-gcp", "environment": "production",
                "deployment_id": "deployment-a", "config_sha256": release_request["config_sha256"],
                "database_version_num": 180003}

    monkeypatch.setattr(sys.modules["vm_release"], "execute", execute)
    assert release_module.main() == 0
    result = json.loads((main_env / "result.json").read_text())
    assert result["state"] == "verified" and calls[0]["config_sha256"] == canonical_hash(release_request["target"])


def test_main_failure_records_deployer_stage_and_code(release_module, main_env, monkeypatch):
    monkeypatch.setattr(sys.modules["vm_release"], "execute", lambda request: {
        "state": "failed", "stage": "database_prepare", "code": "postgres_version", "deployment_id": "deployment-a"})
    assert release_module.main() == 1
    result = json.loads((main_env / "result.json").read_text())
    assert (result["state"], result["stage"], result["code"]) == ("failed", "database_prepare", "postgres_version")
    assert "database_prepare/postgres_version" in result["error"]


def test_main_rejections_record_stage(release_module, main_env, monkeypatch):
    monkeypatch.setenv("TARGET_ID", "missing-target")
    assert release_module.main() == 1
    result = json.loads((main_env / "result.json").read_text())
    assert (result["state"], result["stage"], result["code"]) == ("rejected", "inventory", "inventory_invalid")
    monkeypatch.setenv("TARGET_ID", "fixture-gcp")
    monkeypatch.setenv("APPROVAL", json.dumps({"claims": {}, "signature": "x"}))
    assert release_module.main() == 1
    result = json.loads((main_env / "result.json").read_text())
    assert (result["state"], result["stage"], result["code"]) == ("rejected", "approval", "approval_rejected")
    assert "nonce" not in json.dumps(result)


def test_main_exception_after_reservation_is_unknown(release_module, main_env, monkeypatch):
    def explode(request):
        raise RuntimeError("provider said password=hunter2")

    monkeypatch.setattr(sys.modules["vm_release"], "execute", explode)
    assert release_module.main() == 1
    result = json.loads((main_env / "result.json").read_text())
    assert (result["state"], result["stage"], result["code"]) == ("unknown", "deploy", "deploy_error")
    assert "hunter2" not in json.dumps(result)


def test_documented_examples_are_valid_inventory_and_config(vm_release, release_request):
    from pathlib import Path

    release = Path(__file__).resolve().parents[2] / "release"
    inventory = json.loads((release / "targets.example.json").read_text())
    config = json.loads((release / "config.example.json").read_text())
    for target_id, target in inventory.items():
        assert target["target_id"] == target_id
        vm_release.validate_target(target)
        cloud = target["cloud"]
        bundle = copy.deepcopy(release_request["bundle"])
        request = {**release_request, "target": target, "bundle": bundle, "config_sha256": canonical_hash(target)}
        data = vm_release.deployment_request(request, config)
        assert data["connection"] == config["connections"][cloud]["identity"]
        assert cloud in config["databases"][target["database"]["profile_id"]]["clouds"]
    assert {"state_bucket", "state_region", "work_root"} <= set(config)


def test_same_deployment_id_returns_stored_verified_result_without_redeploy(release_module, release_request,
                                                                          main_env, monkeypatch):
    """응답 유실 뒤 같은 deployment_id 로 다시 부르면 저장된 검증 결과를 돌려주고 배포기를 다시 돌리지 않는다."""
    bundle = release_request["bundle"]
    calls = []

    def execute(request):
        calls.append(request)
        return {**{k: bundle[k] for k in ("commit", "schema", "platform", "images", "image_digests", "bundle_sha256",
                                          "image_ref", "image_digest")},
                "state": "verified", "target_id": "fixture-gcp", "environment": "production",
                "deployment_id": "deployment-a", "config_sha256": release_request["config_sha256"],
                "database_version_num": 180003}

    monkeypatch.setattr(sys.modules["vm_release"], "execute", execute)
    assert release_module.main() == 0
    assert release_module.main() == 0
    assert len(calls) == 1
    assert (main_env / "results" / "deployment-a.json").is_file()
