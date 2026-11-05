"""스테이징 점유: 진행 중인 점검이 쓰는 스테이징(DB 포함)을 다른 실행이 배포·초기화하지 못한다."""

from __future__ import annotations

import asyncio

import pytest

from delta_staging.aws_ecs import Outcome
from delta_staging.config import GcpTarget
from delta_staging.contract import DeployRequest
from delta_staging.registry import Busy, Conflict, StagingRegistry

DIGEST = "sha256:" + "a" * 64
IMAGE = "asia-northeast3-docker.pkg.dev/p/delta-lab-staging/app@" + DIGEST


class FakeProvider:
    def __init__(self):
        self.deploys = []

    def check_request(self, req):
        pass

    async def deploy(self, req, report):
        self.deploys.append(req.run_id)
        report("data_reset", "job: 남은 행 0")
        return Outcome(url="http://s", revision="r", image_digest=DIGEST, task_arn="r", scanner_ip=None)

    async def stop(self):
        pass

    async def endpoint(self):
        return "http://s"


def _req(run_id, holder=None, **kw):
    return DeployRequest(run_id=run_id, cloud="gcp", image=IMAGE, replace=True, lease_holder=holder, **kw)


def test_other_run_cannot_deploy_or_reset_while_leased(tmp_path):
    async def go():
        provider = FakeProvider()
        registry = StagingRegistry({"gcp": provider}, tmp_path / "deployments.json")
        await registry.start(_req("run-a-stage", "run-a"))
        await registry.wait("run-a-stage")
        with pytest.raises(Busy):
            await registry.start(_req("run-b-stage", "run-b"))
        assert provider.deploys == ["run-a-stage"]
        # 같은 실행의 다음 회차 배포는 된다(수정 후 재검증)
        await registry.start(_req("run-a-stage-2", "run-a"))
        await registry.wait("run-a-stage-2")
        # 다른 실행은 남의 점유를 풀지 못하고, 주인이 풀면 다음 실행이 배포할 수 있다
        with pytest.raises(Conflict):
            registry.release("gcp", "run-b")
        assert registry.release("gcp", "run-a") is True
        assert registry.release("gcp", "run-a") is False
        await registry.start(_req("run-b-stage", "run-b"))
        await registry.wait("run-b-stage")
        assert provider.deploys == ["run-a-stage", "run-a-stage-2", "run-b-stage"]
    asyncio.run(go())


def test_lease_survives_restart_and_expires(tmp_path):
    async def go():
        state = tmp_path / "deployments.json"
        registry = StagingRegistry({"gcp": FakeProvider()}, state)
        await registry.start(_req("run-a-stage", "run-a", lease_seconds=60))
        await registry.wait("run-a-stage")
        restarted = StagingRegistry({"gcp": FakeProvider()}, state)
        assert [lease.holder for lease in restarted.leases()] == ["run-a"]
        with pytest.raises(Busy):
            await restarted.start(_req("run-b-stage", "run-b"))
        # 만료된 점유는 막지 않는다(주인이 죽어 풀지 못한 경우)
        lease = restarted._leases["gcp"]
        restarted._leases["gcp"] = lease.model_copy(update={"expires_at": lease.acquired_at})
        assert restarted.leases() == ()
        await restarted.start(_req("run-b-stage", "run-b"))
    asyncio.run(go())


def test_reset_job_only_allowed_on_staging_targets():
    base = {"project_id": "delta-proj", "region": "asia-northeast3", "service": "s", "container_name": "app",
            "address": "ip", "allowed_image_prefixes": ["asia-northeast3-docker.pkg.dev/p/"]}
    assert GcpTarget(**base, reset_job="s-db-reset").environment == "staging"
    with pytest.raises(ValueError, match="staging"):
        GcpTarget(**base, environment="production", reset_job="s-db-reset")
    assert GcpTarget(**base, environment="production").reset_job is None


def test_lease_http_read_token_can_view_and_release_but_not_deploy(tmp_path):
    from fastapi.testclient import TestClient

    from delta_staging.api import create_app

    registry = StagingRegistry({"gcp": FakeProvider()}, tmp_path / "deployments.json")
    app = create_app(registry=registry, read_token="r" * 32, write_token="w" * 32)
    read, write = {"Authorization": "Bearer " + "r" * 32}, {"Authorization": "Bearer " + "w" * 32}
    body = {"run_id": "run-a-stage", "cloud": "gcp", "image": IMAGE, "replace": True, "lease_holder": "run-a"}
    with TestClient(app) as client:
        assert client.post("/v1/staging/deployments", json=body, headers=read).status_code == 403
        assert client.post("/v1/staging/deployments", json=body, headers=write).status_code == 202
        assert [l["holder"] for l in client.get("/v1/staging/leases", headers=read).json()] == ["run-a"]
        other = {**body, "run_id": "run-b-stage", "lease_holder": "run-b"}
        assert client.post("/v1/staging/deployments", json=other, headers=write).status_code == 409
        assert client.post("/v1/staging/leases/gcp/release", json={"holder": "run-b"}, headers=read).status_code == 409
        assert client.post("/v1/staging/leases/gcp/release", json={"holder": "run-a"}, headers=read).json() == {"released": True}
        assert client.post("/v1/staging/leases/gcp/release", json={"holder": "run-a"}).status_code == 401
        assert client.post("/v1/staging/deployments", json=other, headers=write).status_code == 202
