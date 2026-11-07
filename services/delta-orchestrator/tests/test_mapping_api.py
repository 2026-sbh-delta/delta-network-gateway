from __future__ import annotations

from dataclasses import replace
import time

import pytest
from fastapi.testclient import TestClient

from delta_ai.api import create_app as ai_app
from delta_ai.store import RuleStore
from delta_orchestrator.app import create_app
from delta_orchestrator.pipelines import Pipelines, mapping_client
from delta_pipeline.clients import ServiceUnavailable
from test_app import COMMIT, PUBLIC, REPO, fake_run


def mapping_request():
    return {"kind": "migration_mapping", "assessment_id": "input-id", "service": {"service_id": "web"},
            "evidence": {"mapping": {"servers": [{"name": "web", "instance_type": "t4g.nano",
                "multi_arch_image": True, "os": "ubuntu", "instance_profile": False}]}}}


def login(client):
    assert client.post("/login", data={"password": "orchestrator-password-123"},
                       headers={"Origin": PUBLIC}).status_code == 303


@pytest.fixture
def console(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCH_PUBLIC_ORIGIN", PUBLIC)
    monkeypatch.setenv("ORCH_ADMIN_PASSWORD", "orchestrator-password-123")
    monkeypatch.setenv("ORCH_SESSION_SECRET", "s" * 40)
    with TestClient(ai_app(llm=None, store=RuleStore(tmp_path / "ai.sqlite"), token="mapping-secret")) as ai:
        def factory(options, store, run_id):
            run = fake_run(options, store, run_id)
            def assess(body):
                result = ai.post("/v1/assessments", json=body, headers={"Authorization": "Bearer mapping-secret"})
                assert result.status_code == 200
                return result.json()
            run._deps = replace(run._deps, mapping_assess=assess)
            return run
        pipelines = Pipelines(tmp_path / "runs", factory)
        with TestClient(create_app(pipelines), base_url=PUBLIC, follow_redirects=False) as client:
            yield client, pipelines
        if pipelines.run and pipelines.running():
            pipelines.run.answer("stop")
            pipelines._thread.join(timeout=3)
        assert not pipelines.running()


def test_authenticated_api_exposes_saved_prices_and_reload(console):
    client, pipelines = console
    payload = {"repo": REPO, "commit": COMMIT, "mapping_request": mapping_request()}
    headers = {"Origin": PUBLIC, "Idempotency-Key": "mapping-test"}
    assert client.post("/api/pipeline/runs", json=payload, headers=headers).status_code == 401
    login(client)
    assert client.post("/api/pipeline/runs", json=payload, headers={"Origin": "https://evil.example"}).status_code == 403
    started = client.post("/api/pipeline/runs", json=payload, headers=headers)
    assert started.status_code == 202, started.text
    run_id = started.json()["run_id"]
    url = f"/api/pipeline/runs/{run_id}"
    for _ in range(200):
        board = client.get(url).json()
        mapped = next(n for n in board["nodes"] if n["id"] == "map")
        if mapped["state"] == "pass" and any(n.get("decision") for n in board["nodes"]):
            break
        time.sleep(0.01)
    assert mapped["detail"]["mapping"]["cost"]["source_total"] == 3.80
    assert "mapping-secret" not in client.get(url).text
    assert client.post("/api/pipeline/runs", json=payload, headers=headers).json()["run_id"] == run_id
    payload["mapping_request"]["evidence"]["mapping"]["servers"][0]["instance_type"] = "t3.small"
    assert client.post("/api/pipeline/runs", json=payload, headers=headers).status_code == 409
    assert client.post(url + "/answer", json={"value": "stop"}, headers={"Origin": PUBLIC}).status_code == 200
    pipelines._thread.join(timeout=3)
    reloaded = Pipelines(pipelines.store_dir)
    assert reloaded.get(run_id)["nodes"] == client.get(url).json()["nodes"]
    client.cookies.clear()
    assert client.get(url).status_code == 401


def test_invalid_raw_state_or_oversized_input_creates_no_run(console):
    client, pipelines = console
    login(client)
    for value in ({"values": {"secret": "raw"}}, {"kind": "wrong"}, []):
        response = client.post("/api/pipeline/runs", json={"repo": REPO, "commit": COMMIT, "mapping_request": value}, headers={"Origin": PUBLIC})
        assert response.status_code == 400
    # Streamed size limit checks the full body instead of silently truncating JSON.
    response = client.post("/api/pipeline/runs", content='{"padding":"' + 'x' * 64_000 + '"}', headers={"Origin": PUBLIC, "Content-Type": "application/json"})
    assert response.status_code == 413
    assert pipelines.list() == []


def test_mapping_connection_is_server_controlled_and_requires_secure_configuration(monkeypatch):
    for key in ("DELTA_MAPPING_URL", "DELTA_MAPPING_TOKEN", "DELTA_DIAGNOSTIC_URL", "DELTA_DIAGNOSTIC_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ServiceUnavailable):
        mapping_client()
    monkeypatch.setenv("DELTA_MAPPING_URL", "http://external.example")
    monkeypatch.setenv("DELTA_MAPPING_TOKEN", "private-token")
    with pytest.raises(ServiceUnavailable) as exc:
        mapping_client()
    assert "private-token" not in str(exc.value)
    monkeypatch.setenv("DELTA_MAPPING_URL", "https://ai.internal")
    assert mapping_client()._url == "https://ai.internal"
