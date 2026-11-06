"""동규님 platform/deploy/tests/test_database_servers.py 이식 + S3 원격 state·PostgreSQL 18 모듈 확인."""

import json
from pathlib import Path
from uuid import uuid4

import pytest

from release_support import load

RELEASE = Path(__file__).resolve().parents[1]
SELECTED = {"assume_role_arn": "arn:aws:iam::111122223333:role/delta-prod-deploy", "external_id": "x"}


def request(cloud="aws"):
    return {
        "target": {"cloud": cloud, "region": "ap-northeast-2" if cloud == "aws" else "asia-northeast3"},
        "database": {"mode": "new"},
        "connection": {"account_id": "111122223333", "gcp_project": "test-project"},
        "environment_id": str(uuid4()),
        "service_id": str(uuid4()),
    }


def config(root):
    return {"state_bucket": "fixture-state-bucket", "state_region": "ap-northeast-2", "work_root": str(root)}


def test_existing_server_replacement_is_refused_before_apply(tmp_path):
    module = load("database_server")
    calls = []

    def execute(command, **kwargs):
        calls.append(command)
        return json.dumps({"resource_changes": [{"change": {"actions": ["delete", "create"]}}]})

    with pytest.raises(ValueError, match="must not update"):
        module.provision(request(), {"terraform": {"account_id": "111122223333"}}, config(tmp_path), execute, SELECTED)
    assert not any(command[1] == "apply" for command in calls)


def test_remote_state_key_and_target_role_are_used(tmp_path):
    module = load("database_server")
    data, calls = request(), []

    def execute(command, **kwargs):
        calls.append((command, kwargs))
        if command[1] == "show":
            return json.dumps({"resource_changes": [{"change": {"actions": ["create"]}}]})
        if command[1] == "output":
            return json.dumps({"host": {"value": "db.internal"}, "admin_secret_id": {"value": "arn:secret"}})
        return ""

    result = module.provision(data, {"terraform": {"account_id": "111122223333"}, "ca_pem": "CA"},
                              config(tmp_path), execute, SELECTED)
    init = calls[0][0]
    assert init[:2] == ["terraform", "init"]
    assert f"-backend-config=key=delta-cp/release/{data['environment_id']}/database.tfstate" in init
    assert "-backend-config=bucket=fixture-state-bucket" in init and "-backend-config=region=ap-northeast-2" in init
    directory = Path(calls[0][1]["cwd"])
    params = json.loads((directory / "server.auto.tfvars.json").read_text())
    assert params["assume_role_arn"] == SELECTED["assume_role_arn"] and params["region"] == "ap-northeast-2"
    assert not (directory / "terraform.tfstate").exists()
    assert result["secret_provider"] == "aws" and result["connection"]["host"] == "db.internal"


def test_gcp_password_is_ephemeral_and_stable_across_retry(tmp_path):
    module = load("database_server")
    passwords, data = [], request("gcp")

    def execute(command, **kwargs):
        passwords.append(kwargs["env"]["TF_VAR_admin_password"])
        if command[1] == "show":
            return json.dumps({"resource_changes": [{"change": {"actions": ["create"]}}]})
        if command[1] == "output":
            return json.dumps({"host": {"value": "db.sql.goog"}, "private_ip": {"value": "10.32.3.2"},
                               "admin_secret_id": {"value": "projects/test-project/secrets/admin"}})
        return ""

    for _ in range(2):
        result = module.provision(data, {"terraform": {"project_id": "test-project"}, "ca_pem": "PUBLIC CA"},
                                  config(tmp_path), execute, SELECTED)
    directory = tmp_path / data["environment_id"] / "database"
    assert len(set(passwords)) == 1
    assert passwords[0] not in (directory / "server.auto.tfvars.json").read_text()
    assert (directory / "admin-password").stat().st_mode & 0o777 == 0o600
    assert passwords[0] not in json.dumps(result)
    assert result["secret_provider"] == "gcp"
    assert result["iap_endpoint"] == {"address": "10.32.3.2", "port": 5432}


def test_wrong_account_and_existing_database_cannot_provision(tmp_path):
    module = load("database_server")
    data = request()
    with pytest.raises(ValueError, match="selected cloud account"):
        module.provision(data, {"terraform": {"account_id": "999999999999"}}, config(tmp_path), None, SELECTED)
    data["database"]["mode"] = "existing"
    with pytest.raises(ValueError, match="new database"):
        module.provision(data, {}, config(tmp_path), None, SELECTED)


@pytest.mark.parametrize("module_dir,needle", [
    ("db-aws", 'engine_version              = "18"'),
    ("db-aws", 'family = "postgres18"'),
    ("db-gcp", 'database_version    = "POSTGRES_18"'),
])
def test_database_modules_are_postgres18_with_remote_backend(module_dir, needle):
    text = (RELEASE / "terraform" / module_dir / "main.tf").read_text()
    assert needle in text
    assert 'backend "s3" {}' in text
    assert "17" not in text.replace("POSTGRES_18", "")


@pytest.mark.parametrize("module_dir", ["vm-aws", "vm-gcp", "db-aws", "db-gcp"])
def test_every_module_uses_remote_backend_and_lockfile(module_dir):
    directory = RELEASE / "terraform" / module_dir
    assert 'backend "s3" {}' in (directory / "main.tf").read_text()
    assert (directory / ".terraform.lock.hcl").exists()
