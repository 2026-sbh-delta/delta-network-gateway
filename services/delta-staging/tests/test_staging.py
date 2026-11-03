from __future__ import annotations

import json

import pytest
from conftest import ECR, IMAGE, FakeEc2, FakeEcs, FakeElb, FakeSts, make_provider
from pydantic import ValidationError

from delta_staging.config import load_targets
from delta_staging.contract import DeployRequest
from delta_staging.registry import Busy, Conflict, RequestRejected, StagingRegistry, Unsupported


def _req(**overrides) -> DeployRequest:
    return DeployRequest.model_validate({"run_id": "run-1", "image": IMAGE, "commit": "abc1234", **overrides})


async def test_deploy_swaps_image_and_reports_internal_url(target):
    ecs = FakeEcs()
    registry = StagingRegistry({"aws": make_provider(target, ecs=ecs)})

    started = await registry.start(_req(env={"FEATURE_X": "on"}))
    done = await registry.wait("run-1")

    assert started.state == "deploying"
    assert done.state == "ready" and done.url == target.url and done.revision.endswith(":2")
    registered = next(kwargs for name, kwargs in ecs.calls if name == "register_task_definition")
    container = registered["containerDefinitions"][0]
    env = {item["name"]: item["value"] for item in container["environment"]}
    assert container["image"] == IMAGE
    assert env == {"APP_MODE": "staging", "FEATURE_X": "on", "DELTA_RUN_ID": "run-1", "DELTA_COMMIT": "abc1234"}
    assert "taskDefinitionArn" not in registered and "status" not in registered
    update = next(kwargs for name, kwargs in ecs.calls if name == "update_service")
    assert update["desiredCount"] == 1 and update["taskDefinition"] == done.revision
    assert [s.name for s in done.steps] == ["accepted", "task_definition", "service_updated", "image_digest", "healthy"]
    assert done.steps[0].elapsed_seconds == 0 and all(s.elapsed_seconds >= 0 for s in done.steps)
    assert done.image_digest == "sha256:" + "a" * 64 and done.task_arn.endswith("/abc")


async def test_tag_image_records_digest_from_running_task(target):
    registry = StagingRegistry({"aws": make_provider(target, ecs=FakeEcs())})

    await registry.start(_req(image=ECR + "demo-app:v1"))
    done = await registry.wait("run-1")

    assert done.state == "ready" and not done.pinned
    assert done.image_digest == "sha256:" + "b" * 64  # 요청에는 없던 값을 실행 중인 태스크에서 읽었다


async def test_running_digest_mismatch_fails(target):
    ecs = FakeEcs(running_digest="sha256:" + "c" * 64)
    registry = StagingRegistry({"aws": make_provider(target, ecs=ecs)})

    await registry.start(_req())
    done = await registry.wait("run-1")

    assert done.state == "failed" and "digest" in done.error and done.image_digest is None


async def test_image_outside_allowed_registry_is_rejected_before_any_call(target):
    ecs = FakeEcs()
    registry = StagingRegistry({"aws": make_provider(target, ecs=ecs)})

    with pytest.raises(RequestRejected):
        await registry.start(_req(image="ghcr.io/someone/app:latest"))
    assert ecs.calls == []


async def test_rollout_failure_marks_failed_and_scales_down(target):
    ecs = FakeEcs(rollout=["FAILED"], failure_reason="tasks failed to start")
    registry = StagingRegistry({"aws": make_provider(target, ecs=ecs)})

    await registry.start(_req())
    done = await registry.wait("run-1")

    assert done.state == "failed" and "tasks failed to start" in done.error and done.url is None
    assert ecs.calls[-1] == ("update_service", {"cluster": target.cluster, "service": target.service, "desiredCount": 0})


async def test_ready_waits_until_old_task_stops_serving(target):
    elb = FakeElb(states=["healthy"], old=["healthy", "healthy", "draining"])
    registry = StagingRegistry({"aws": make_provider(target, elb=elb)})

    await registry.start(_req())
    done = await registry.wait("run-1")

    assert done.state == "ready" and elb.old == ["draining"]  # 이전 태스크가 트래픽에서 빠진 뒤에야 ready


async def test_ready_does_not_wait_for_ecs_completed_mark(target):
    ecs = FakeEcs(rollout=["IN_PROGRESS"])
    registry = StagingRegistry({"aws": make_provider(target, ecs=ecs)})

    await registry.start(_req())
    done = await registry.wait("run-1")

    assert done.state == "ready"


async def test_old_task_still_serving_times_out(target):
    elb = FakeElb(states=["healthy"], old=["healthy"])
    registry = StagingRegistry({"aws": make_provider(target, elb=elb, health_timeout=0)})

    await registry.start(_req())
    done = await registry.wait("run-1")

    assert done.state == "failed" and "10.0.1.10=healthy" in done.error


async def test_unhealthy_target_times_out(target):
    registry = StagingRegistry({"aws": make_provider(target, elb=FakeElb(["unhealthy"]), health_timeout=0)})

    await registry.start(_req())
    done = await registry.wait("run-1")

    assert done.state == "failed" and "unhealthy" in done.error


async def test_wrong_account_never_touches_ecs(target):
    ecs = FakeEcs()
    registry = StagingRegistry({"aws": make_provider(target, ecs=ecs, sts=FakeSts("111122223333"))})

    await registry.start(_req())
    done = await registry.wait("run-1")

    assert done.state == "failed" and "계정" in done.error
    assert [name for name, _ in ecs.calls] == ["update_service"]  # 실패 처리의 desired=0 만


async def test_same_run_id_is_idempotent_and_different_image_conflicts(target):
    ecs = FakeEcs()
    registry = StagingRegistry({"aws": make_provider(target, ecs=ecs)})
    await registry.start(_req())
    await registry.wait("run-1")

    again = await registry.start(_req())

    assert again.state == "ready"
    assert sum(1 for name, _ in ecs.calls if name == "register_task_definition") == 1
    with pytest.raises(Conflict):
        await registry.start(_req(image=ECR + "demo-app@sha256:" + "b" * 64))


async def test_second_run_waits_unless_replace(target):
    registry = StagingRegistry({"aws": make_provider(target)})
    await registry.start(_req())
    await registry.wait("run-1")

    with pytest.raises(Busy):
        await registry.start(_req(run_id="run-2"))
    await registry.start(_req(run_id="run-2", replace=True))
    await registry.wait("run-2")

    assert registry.get("run-1").state == "replaced" and registry.get("run-1").url is None
    assert registry.get("run-2").state == "ready"


async def test_stop_scales_to_zero(target):
    ecs = FakeEcs()
    registry = StagingRegistry({"aws": make_provider(target, ecs=ecs)})
    await registry.start(_req())
    await registry.wait("run-1")

    stopped = await registry.stop("run-1")

    assert stopped.state == "stopped" and stopped.url is None
    assert ecs.calls[-1][1]["desiredCount"] == 0


async def test_unconfigured_cloud_is_unsupported(target):
    registry = StagingRegistry({"aws": make_provider(target)})

    with pytest.raises(Unsupported):
        await registry.start(_req(cloud="gcp"))


@pytest.mark.parametrize("env", [{"DB_PASSWORD": "x"}, {"API_KEY": "x"}, {"lower": "x"}, {"A": "x" * 5000}])
def test_secret_or_malformed_env_is_rejected(env):
    with pytest.raises(ValidationError):
        _req(env=env)


def test_tag_image_is_allowed_but_not_pinned():
    assert not _req(image=ECR + "demo-app:latest").pinned
    assert _req().pinned


async def test_restart_marks_unfinished_deploy_failed(target, tmp_path):
    state = tmp_path / "deployments.json"
    registry = StagingRegistry({"aws": make_provider(target)}, state)
    await registry.start(_req())
    await registry.wait("run-1")
    data = json.loads(state.read_text(encoding="utf-8"))
    data[0]["state"] = "deploying"
    state.write_text(json.dumps(data), encoding="utf-8")

    reloaded = StagingRegistry({"aws": make_provider(target)}, state)

    assert reloaded.get("run-1").state == "failed" and "재시작" in reloaded.get("run-1").error


def test_load_targets_reads_terraform_tool_config(tmp_path, target):
    path = tmp_path / "staging.json"
    path.write_text(json.dumps(target.model_dump(mode="json")), encoding="utf-8")

    assert load_targets(path)["aws"] == target


async def test_auto_scanner_ip_opens_only_the_current_runner(public_target):
    ec2 = FakeEc2()
    registry = StagingRegistry({"aws": make_provider(public_target, ec2=ec2, public_ip="52.78.10.20")})

    await registry.start(_req(scanner_ip="auto"))
    done = await registry.wait("run-1")

    assert done.state == "ready" and done.scanner_ip == "52.78.10.20"
    assert [rule["CidrIpv4"] for rule in ec2.rules] == ["52.78.10.20/32"]
    assert "scanner_allowed" in [step.name for step in done.steps]


async def test_new_runner_ip_replaces_the_old_rule(public_target):
    ec2 = FakeEc2()
    registry = StagingRegistry({"aws": make_provider(public_target, ec2=ec2, public_ip="52.78.10.20")})
    await registry.start(_req(scanner_ip="auto"))
    await registry.wait("run-1")

    await registry.start(_req(run_id="run-2", scanner_ip="3.35.120.9", replace=True))
    await registry.wait("run-2")

    assert [rule["CidrIpv4"] for rule in ec2.rules] == ["3.35.120.9/32"]


async def test_stop_closes_the_runner_ip(public_target):
    ec2 = FakeEc2()
    registry = StagingRegistry({"aws": make_provider(public_target, ec2=ec2)})
    await registry.start(_req(scanner_ip="auto"))
    await registry.wait("run-1")

    await registry.stop("run-1")

    assert ec2.rules == []


async def test_internal_staging_refuses_scanner_ip(target):
    registry = StagingRegistry({"aws": make_provider(target)})

    with pytest.raises(RequestRejected):
        await registry.start(_req(scanner_ip="auto"))


@pytest.mark.parametrize("ip", ["10.0.0.1", "127.0.0.1", "::1", "not-an-ip"])
def test_scanner_ip_must_be_public_ipv4_or_auto(ip):
    with pytest.raises(ValidationError):
        _req(scanner_ip=ip)
