from __future__ import annotations

import pytest
from conftest import IMAGE, make_provider
from fastapi.testclient import TestClient

from delta_staging.api import create_app
from delta_staging.registry import StagingRegistry
from delta_staging.tools import tool_catalog

READ = "read-token-value-0123"
WRITE = "write-token-value-4567"
OLD_SHARED = "old-shared-token-89ab"
AUTH = {"Authorization": f"Bearer {WRITE}"}
READ_AUTH = {"Authorization": f"Bearer {READ}"}


def make_client(target, registry=None):
    registry = registry or StagingRegistry({"aws": make_provider(target)})
    return TestClient(create_app(registry=registry, read_token=READ, write_token=WRITE))


@pytest.fixture
def client(target):
    return make_client(target)


@pytest.mark.parametrize("read, write", [("", WRITE), (READ, ""), ("", ""), (READ, READ), ("short", WRITE)])
def test_app_refuses_unsafe_token_setup(read, write):
    with pytest.raises(ValueError):
        create_app(registry=StagingRegistry({}), read_token=read, write_token=write)


def test_endpoints_require_token(client):
    assert client.get("/v1/tools").status_code == 401
    assert client.post("/v1/staging/deployments", json={"run_id": "r", "image": IMAGE}).status_code == 401
    assert client.get("/v1/staging/deployments/r", headers={"Authorization": "Bearer wrong-token-xxxxxxxx"}).status_code == 401
    assert client.get("/healthz").json() == {"status": "ok"}


def test_old_shared_token_is_no_longer_accepted(client):
    old = {"Authorization": f"Bearer {OLD_SHARED}"}
    assert client.get("/v1/staging/targets", headers=old).status_code == 401
    assert client.post("/v1/staging/deployments", json={"run_id": "r", "image": IMAGE}, headers=old).status_code == 401


class SpyRegistry(StagingRegistry):
    """배포·중단 함수가 실제로 불렸는지 기록한다."""

    def __init__(self, providers):
        super().__init__(providers)
        self.calls: list[str] = []

    async def start(self, req):
        self.calls.append("start")
        return await super().start(req)

    async def stop(self, run_id):
        self.calls.append("stop")
        return await super().stop(run_id)


def test_read_token_reads_but_cannot_change(target):
    registry = SpyRegistry({"aws": make_provider(target)})
    client = make_client(target, registry)
    assert client.post("/v1/staging/deployments", json={"run_id": "r1", "image": IMAGE}, headers=AUTH).status_code == 202
    registry.calls.clear()

    assert client.get("/v1/staging/targets", headers=READ_AUTH).status_code == 200
    assert client.get("/v1/staging/deployments/r1", headers=READ_AUTH).status_code == 200
    assert client.get("/v1/staging/deployments", headers=READ_AUTH).status_code == 200
    assert client.get("/v1/tools", headers=READ_AUTH).status_code == 200

    assert client.post("/v1/staging/deployments", json={"run_id": "r2", "image": IMAGE}, headers=READ_AUTH).status_code == 403
    assert client.post("/v1/staging/deployments/r1/stop", headers=READ_AUTH).status_code == 403
    assert registry.calls == []

    assert client.post("/v1/staging/deployments/r1/stop", headers=AUTH).status_code == 200
    assert registry.calls == ["stop"]


def test_deploy_returns_202_and_status_is_queryable(client):
    response = client.post("/v1/staging/deployments", json={"run_id": "r1", "image": IMAGE}, headers=AUTH)

    assert response.status_code == 202
    assert response.json()["state"] == "deploying"
    assert client.get("/v1/staging/deployments/r1", headers=AUTH).json()["run_id"] == "r1"
    assert client.get("/v1/staging/deployments/none", headers=AUTH).status_code == 404


def test_error_codes(client):
    rejected = {"run_id": "r1", "image": "ghcr.io/someone/app:latest"}
    gcp = {"run_id": "r2", "image": IMAGE, "cloud": "gcp"}
    secret = {"run_id": "r3", "image": IMAGE, "env": {"DB_PASSWORD": "x"}}

    assert client.post("/v1/staging/deployments", json=rejected, headers=AUTH).status_code == 400
    assert client.post("/v1/staging/deployments", json=gcp, headers=AUTH).status_code == 501
    assert client.post("/v1/staging/deployments", json=secret, headers=AUTH).status_code == 422


def test_tool_catalog_is_llm_tool_shaped(client):
    catalog = client.get("/v1/tools", headers=AUTH).json()

    assert catalog == tool_catalog()
    assert [tool["name"] for tool in catalog["tools"]] == ["staging_deploy", "staging_status", "staging_stop"]
    assert all(set(tool) == {"name", "description", "input_schema"} for tool in catalog["tools"])
    deploy = catalog["tools"][0]["input_schema"]
    assert deploy["additionalProperties"] is False and set(deploy["required"]) == {"run_id", "image"}
    assert set(catalog["routes"]) == {tool["name"] for tool in catalog["tools"]}


def test_targets_report_the_live_alb_address(target):
    # 설정에 url 이 없으면 대상 그룹이 붙은 ALB 를 조회한다. 인프라를 다시 만들어도 DAST 설정을 고치지 않는다.
    provider = make_provider(target.model_copy(update={"url": None}))
    app = make_client(target, StagingRegistry({"aws": provider}))
    assert app.get("/v1/staging/targets").status_code == 401
    assert app.get("/v1/staging/targets", headers=READ_AUTH).json() == {
        "targets": [{"cloud": "aws", "url": "http://delta-lab-staging-alb-1.ap-northeast-2.elb.amazonaws.com"}]}
