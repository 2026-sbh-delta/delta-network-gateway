"""S3 원격 state 키·init 인자, 작업 디렉터리, 대상 카탈로그, 원인 코드."""

import pytest

from release_support import ENVIRONMENT_ID, load


def test_state_key_and_init_arguments(config):
    module = load("state_backend")
    assert module.state_key(ENVIRONMENT_ID, "vm") == f"delta-cp/release/{ENVIRONMENT_ID}/vm.tfstate"
    args = module.init_command(config, ENVIRONMENT_ID, "database")
    assert args[:2] == ["terraform", "init"]
    assert {"-backend-config=bucket=fixture-state-bucket", "-backend-config=region=ap-northeast-2",
            f"-backend-config=key=delta-cp/release/{ENVIRONMENT_ID}/database.tfstate",
            "-backend-config=encrypt=true", "-backend-config=use_lockfile=true"} <= set(args)


@pytest.mark.parametrize("change", [{"state_bucket": None}, {"state_bucket": "Bad_Bucket"}, {"state_region": "x"}])
def test_missing_or_invalid_backend_config_is_refused(config, change):
    module = load("state_backend")
    with pytest.raises(ValueError):
        module.init_command({**config, **change}, ENVIRONMENT_ID, "vm")


def test_non_uuid_environment_and_unknown_module_are_refused():
    module = load("state_backend")
    with pytest.raises(ValueError):
        module.state_key("../other", "vm")
    with pytest.raises(ValueError):
        module.state_key(ENVIRONMENT_ID, "network")


def test_workdir_refreshes_module_files(config):
    module = load("state_backend")
    directory = module.workdir(config, ENVIRONMENT_ID, "vm", "gcp")
    (directory / "stale.tf").write_text("resource x y {}")
    directory = module.workdir(config, ENVIRONMENT_ID, "vm", "gcp")
    assert not (directory / "stale.tf").exists()
    assert {"main.tf", "variables.tf", "outputs.tf", ".terraform.lock.hcl"} <= {p.name for p in directory.iterdir()}
    assert directory.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("target,ok", [
    ({"cloud": "aws", "region": "ap-northeast-2", "profile_id": "t3.small"}, True),
    ({"cloud": "gcp", "region": "asia-northeast3", "profile_id": "e2-small"}, True),
    ({"cloud": "onprem", "region": "onprem-local", "profile_id": "small", "host_id": "delta-onprem"}, True),
    ({"cloud": "gcp", "region": "us-central1", "profile_id": "e2-small"}, False),
    ({"cloud": "aws", "region": "ap-northeast-2", "profile_id": "t3.micro"}, False),
    ({"cloud": "aws", "region": "ap-northeast-2", "profile_id": "t3.small", "vm": "x"}, False),
    ({"cloud": "onprem", "region": "onprem-local", "profile_id": "small"}, False),
])
def test_catalog_matches_supported_targets(target, ok):
    module = load("targets")
    if ok:
        assert module.DeploymentTarget.model_validate(target).cloud == target["cloud"]
    else:
        with pytest.raises(ValueError):
            module.DeploymentTarget.model_validate(target)


def test_command_error_code_uses_tool_and_subcommand_only():
    module = load("errors")
    assert module.CommandError(["/usr/local/bin/terraform", "apply", "-input=false"]).code == "terraform_apply_failed"
    assert module.CommandError(["gcloud", "--quiet"]).code == "gcloud_failed"
    with pytest.raises(module.DeployError) as error:
        with module.step("vm_runtime", "vm_runtime_failed"):
            raise RuntimeError("provider said password=hunter2")
    assert (error.value.stage, error.value.code) == ("vm_runtime", "vm_runtime_failed")
    assert "hunter2" not in str(error.value)
