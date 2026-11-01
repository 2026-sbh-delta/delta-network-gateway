from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from delta_ai.api import create_app
from delta_ai.fake_llm import FakeLLM
from delta_ai.store import RuleStore

TOKEN = "test-token-value"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(llm=FakeLLM(), store=RuleStore(tmp_path / "rules.json"), token=TOKEN))


def test_app_refuses_to_start_without_token(tmp_path):
    with pytest.raises(ValueError):
        create_app(llm=None, store=RuleStore(tmp_path / "rules.json"), token="")


def test_assessment_requires_bearer_token(client, parity_ok):
    assert client.post("/v1/assessments", json=parity_ok).status_code == 401
    assert client.post("/v1/assessments", json=parity_ok,
                       headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_healthz_needs_no_token(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_assessment_returns_contract_shape(client, parity_waf_missing):
    response = client.post("/v1/assessments", json=parity_waf_missing, headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["assessment_id"] == "as-parity-waf-missing"
    assert body["verdict"] == "fix"
    assert body["checks"]
    assert body["remediation"]["waf_rules"]


def test_invalid_kind_returns_422(client, parity_ok):
    assert client.post("/v1/assessments", json={**parity_ok, "kind": "nope"}, headers=AUTH).status_code == 422


def test_parity_without_envs_returns_422(client):
    raw = {"assessment_id": "a", "kind": "migration_parity", "service": {"service_id": "s"}, "evidence": {}}

    assert client.post("/v1/assessments", json=raw, headers=AUTH).status_code == 422


def test_removed_code_security_kind_returns_422(client, parity_ok):
    assert client.post("/v1/assessments", json={**parity_ok, "kind": "code_security"}, headers=AUTH).status_code == 422


def test_candidate_approval_flow(client, feedback_request):
    ingest = client.post("/v1/assessments", json=feedback_request, headers=AUTH).json()
    candidate_id = ingest["rule_candidates"][0]["candidate_id"]

    response = client.post(f"/v1/rule-candidates/{candidate_id}/decision",
                           json={"decision": "approved", "approver": "yeonjae"}, headers=AUTH)

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    listed = client.get("/v1/rule-candidates", headers=AUTH).json()
    assert listed[0]["approver"] == "yeonjae"


def test_unknown_candidate_returns_404(client):
    response = client.post("/v1/rule-candidates/none/decision",
                           json={"decision": "approved", "approver": "yeonjae"}, headers=AUTH)

    assert response.status_code == 404


def test_unready_candidate_returns_409(tmp_path, feedback_request):
    client = TestClient(create_app(llm=None, store=RuleStore(tmp_path / "rules.json"), token=TOKEN))
    ingest = client.post("/v1/assessments", json=feedback_request, headers=AUTH).json()
    candidate_id = ingest["rule_candidates"][0]["candidate_id"]

    response = client.post(f"/v1/rule-candidates/{candidate_id}/decision",
                           json={"decision": "approved", "approver": "yeonjae"}, headers=AUTH)

    assert response.status_code == 409
