from __future__ import annotations

import pytest
from conftest import IMAGE, make_provider
from fastapi.testclient import TestClient

from delta_staging.api import create_app
from delta_staging.registry import StagingRegistry
from delta_staging.tools import tool_catalog

TOKEN = "test-token-value"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(target):
    return TestClient(create_app(registry=StagingRegistry({"aws": make_provider(target)}), token=TOKEN))


def test_app_refuses_to_start_without_token(target):
    with pytest.raises(ValueError):
        create_app(registry=StagingRegistry({}), token="")


def test_endpoints_require_token(client):
    assert client.get("/v1/tools").status_code == 401
    assert client.post("/v1/staging/deployments", json={"run_id": "r", "image": IMAGE}).status_code == 401
    assert client.get("/healthz").json() == {"status": "ok"}


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
