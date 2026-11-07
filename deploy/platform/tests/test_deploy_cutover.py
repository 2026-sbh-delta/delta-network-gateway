"""Run the deployment entry point with local AWS and Terraform substitutes."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


DEPLOY = Path(__file__).resolve().parents[1]
PARAMETER = "/delta-cp/orchestrator/cutover_config"
TOKEN = "/delta-cp/orchestrator/cloudflare_token"
CURRENT = {
    "record": "demo.delta.mungji.com",
    "zone_id": "user-zone",
    "green": "aws.adajosou.site",
    "blue": "gcp.adajosou.site",
}
STALE = {**CURRENT, "record": "app.adajosou.site", "zone_id": "previous-zone"}

AWS = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

path = Path(os.environ["CUTOVER_TEST_STATE"])
state = json.loads(path.read_text())
args = sys.argv[1:]
state["calls"].append(args)

def save():
    path.write_text(json.dumps(state))

def option(name):
    return args[args.index(name) + 1]

def fail(code):
    save()
    print("An error occurred (" + code + ") when calling PutParameter", file=sys.stderr)
    sys.exit(254)

if args[:2] == ["ssm", "get-parameter"]:
    name = option("--name")
    if name.endswith("/cutover_config") and name not in state["parameters"]:
        fail("ParameterNotFound")
    print(name)
elif args[:2] == ["ssm", "put-parameter"]:
    name = option("--name")
    if state.get("failure"):
        fail(state["failure"])
    if state.get("race"):
        state["parameters"][name] = {"value": state.pop("race"), "version": 1}
    previous = state["parameters"].get(name)
    if previous and "--overwrite" not in args:
        fail("ParameterAlreadyExists")
    version = previous["version"] + 1 if previous else 1
    state["parameters"][name] = {"value": option("--value"), "version": version}
    print(json.dumps({"Version": version}))
elif args[:2] == ["ec2", "describe-instances"]:
    print("i-test")
elif args[:2] == ["ecr", "describe-repositories"]:
    print("123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/test-orchestrator")
elif args[:2] == ["ecr", "describe-images"]:
    print("sha256:" + "a" * 64)
elif args[:2] == ["ssm", "send-command"]:
    print("test-command")
elif args[:2] == ["ssm", "get-command-invocation"]:
    print(json.dumps({"Status": "Success", "ResponseCode": 0,
                      "StandardOutputContent": "ok", "StandardErrorContent": ""}))
else:
    raise SystemExit("Unexpected AWS operation")
save()
'''

TERRAFORM = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

state = json.loads(Path(os.environ["CUTOVER_TEST_STATE"]).read_text())
root = sys.argv[1]
if root.endswith("/platform-aws"):
    print(json.dumps({"region": {"value": "ap-northeast-2"},
                      "parameter_prefix": {"value": "/delta-cp/"},
                      "app_urls": {"value": {"orchestrator": "https://platform.example"}},
                      "nat_egress_ip": {"value": "192.0.2.1"},
                      "llm_role_arn": {"value": "arn:aws:iam::123456789012:role/test"}}))
elif root.endswith("/onboarding-gcp"):
    print("{}")
elif root.endswith("/service-domain"):
    if state["terraform_config"] is None:
        raise SystemExit(1)
    print(state["terraform_config"])
else:
    raise SystemExit("Unexpected Terraform operation")
'''


@pytest.fixture
def deployment(tmp_path):
    repo = tmp_path / "repo"
    scripts = repo / "deploy/platform"
    scripts.mkdir(parents=True)
    for name in ("deploy.sh", "ssmcheck.py", "remote-common.sh", "remote-orchestrator.sh"):
        shutil.copyfile(DEPLOY / name, scripts / name)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    for name, content in (("aws", AWS), ("terraform", TERRAFORM)):
        executable = binaries / name
        executable.write_text(content)
        executable.chmod(0o755)
    state_file = tmp_path / "state.json"

    def run(existing=None, *, terraform_config=json.dumps(STALE), failure=None, race=None):
        parameters = {TOKEN: {"value": "test-secret-sentinel", "version": 4}}
        if existing is not None:
            parameters[PARAMETER] = {"value": existing, "version": 10}
        state_file.write_text(json.dumps({"parameters": parameters, "calls": [],
                                          "terraform_config": terraform_config,
                                          "failure": failure, "race": race}))
        env = {**os.environ, "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
               "CUTOVER_TEST_STATE": str(state_file), "AWS_PROFILE": "test", "TAG": "existing-tag"}
        result = subprocess.run(["bash", str(scripts / "deploy.sh"), "orchestrator"],
                                env=env, capture_output=True, text=True, timeout=20)
        state = json.loads(state_file.read_text())
        assert state["parameters"][TOKEN] == parameters[TOKEN]
        assert "test-secret-sentinel" not in result.stdout + result.stderr
        return result, state

    return run


@pytest.mark.parametrize("existing", [json.dumps(CURRENT), "disabled: maintenance"])
def test_redeployment_preserves_existing_operator_setting(deployment, existing):
    result, state = deployment(existing)
    assert result.returncode == 0, result.stderr
    assert state["parameters"][PARAMETER] == {"value": existing, "version": 10}
    assert any(call[:2] == ["ssm", "send-command"] for call in state["calls"])


def test_first_deployment_initializes_from_terraform(deployment):
    result, state = deployment()
    assert result.returncode == 0, result.stderr
    assert json.loads(state["parameters"][PARAMETER]["value"]) == STALE
    assert state["parameters"][PARAMETER]["version"] == 1


def test_concurrent_creation_preserves_the_new_operator_setting(deployment):
    current = json.dumps(CURRENT)
    result, state = deployment(race=current)
    assert result.returncode == 0, result.stderr
    assert state["parameters"][PARAMETER] == {"value": current, "version": 1}


@pytest.mark.parametrize("failure", ["AccessDeniedException", "ThrottlingException"])
def test_parameter_write_failure_stops_before_container_replacement(deployment, failure):
    result, state = deployment(json.dumps(CURRENT), failure=failure)
    assert result.returncode != 0
    assert failure in result.stderr
    assert state["parameters"][PARAMETER]["version"] == 10
    assert not any(call[:2] == ["ssm", "send-command"] for call in state["calls"])


def test_missing_terraform_output_keeps_existing_configuration(deployment):
    current = json.dumps(CURRENT)
    result, state = deployment(current, terraform_config=None)
    assert result.returncode == 0, result.stderr
    assert state["parameters"][PARAMETER] == {"value": current, "version": 10}
