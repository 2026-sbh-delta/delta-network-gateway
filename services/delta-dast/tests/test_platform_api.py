import asyncio
import copy
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException

from dast_agent.platform_api import PlatformRuns, fingerprint
from dast_agent.registry import load_catalog


def payload():
    report = json.loads((Path(__file__).parents[1] / "examples/delta-sast-board.sarif").read_text())
    return {"binding": {"build_id": str(uuid4()), "sast_run_id": str(uuid4()),
                        "commit_sha": "a" * 40, "image": "registry/app@sha256:" + "b" * 64,
                        "image_digest": "sha256:" + "b" * 64,
                        "sast_evidence_sha256": fingerprint(report),
                        "target_url": "http://127.0.0.1:8766"}, "report": report}


def fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("DAST_PLATFORM_TARGET_ID", "local-fixture")
    live, calls = {}, []

    def launch(args, extra, *, run_id):
        calls.append((args, extra, run_id))
        live[run_id] = {"status": "running", "results": []}

    async def cancel(run_id):
        if run_id in live:
            live[run_id]["status"] = "error"

    registry = PlatformRuns(tmp_path, load_catalog(), launch, live.get, cancel, 8766)
    return registry, live, calls


def test_idempotent_requests_are_pinned_and_never_auto_fix(tmp_path, monkeypatch):
    registry, live, calls = fixture(tmp_path, monkeypatch)
    body, run_id = payload(), str(uuid4())

    async def scenario():
        results = await asyncio.gather(*(registry.submit(run_id, body) for _ in range(4)))
        assert all(r["binding"] == body["binding"] for r in results)
        assert len(calls) == 1
        assert calls[0][0].auto_fix is False and calls[0][0].impact is False
        assert calls[0][0].target_id == "local-fixture"
        assert calls[0][0].planner == "agent"
        other = copy.deepcopy(body)
        other["binding"]["commit_sha"] = "c" * 40
        with pytest.raises(HTTPException) as exc:
            await registry.submit(run_id, other)
        assert exc.value.status_code == 409
        live.clear()  # Equivalent to scanner restart: no in-memory execution remains.
        result = await registry.submit(run_id, body)
        assert result["status"] == "interrupted" and len(calls) == 1

    asyncio.run(scenario())


def test_cancel_before_submit_tombstones_delayed_requests(tmp_path, monkeypatch):
    registry, _, calls = fixture(tmp_path, monkeypatch)
    run_id = str(uuid4())

    async def scenario():
        assert (await registry.stop(run_id))["stopped"]
        with pytest.raises(HTTPException) as exc:
            await registry.submit(run_id, payload())
        assert exc.value.status_code == 409
        assert calls == []

    asyncio.run(scenario())


def test_zero_supported_candidates_is_skipped_not_safe(tmp_path, monkeypatch):
    registry, _, calls = fixture(tmp_path, monkeypatch)
    body = payload()
    body["report"]["runs"][0]["results"] = []
    body["binding"]["sast_evidence_sha256"] = fingerprint(body["report"])
    result = asyncio.run(registry.submit(str(uuid4()), body))
    assert result["status"] == "skipped" and not result["deployment_approved"]
    assert result["finding_ids"] == [] and not calls


@pytest.mark.parametrize("field,value", [("target_url", "http://169.254.169.254"),
                                        ("sast_evidence_sha256", "d" * 64),
                                        ("image", "registry/app:latest")])
def test_wrong_target_or_evidence_never_launches(tmp_path, monkeypatch, field, value):
    registry, _, calls = fixture(tmp_path, monkeypatch)
    body = payload()
    body["binding"][field] = value
    with pytest.raises(ValueError):
        asyncio.run(registry.submit(str(uuid4()), body))
    assert not calls


def test_result_excludes_raw_evidence_and_cancel_waits(tmp_path, monkeypatch):
    registry, live, _ = fixture(tmp_path, monkeypatch)
    run_id = str(uuid4())
    asyncio.run(registry.submit(run_id, payload()))
    live[run_id] = {"status": "done", "results": [{"finding_id": "abc", "status": "REPRODUCED",
                                                   "evidence": {"payload": "secret browser content"}}]}
    result = registry.view(run_id)
    assert "secret browser content" not in json.dumps(result)
    assert len(result["results"][0]["evidence_sha256"]) == 64
    assert asyncio.run(registry.stop(run_id))["stopped"]


def test_http_surface_requires_separate_token(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from dast_agent.server import create_app

    monkeypatch.setenv("DAST_PLATFORM_TOKEN", "test-only-platform-token")
    monkeypatch.setenv("DAST_PLATFORM_TARGET_ID", "local-fixture")
    app = create_app(runs_dir=tmp_path)
    run_id = str(uuid4())
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert client.get(f"/api/platform/runs/{run_id}").status_code == 401
        client.headers["Authorization"] = "Bearer test-only-platform-token"
        assert client.get(f"/api/platform/runs/{run_id}").status_code == 404
        assert client.post(f"/api/platform/runs/{run_id}/cancel").json()["stopped"]
        assert client.put(f"/api/platform/runs/{run_id}", json=payload()).status_code == 409
        body = payload()
        body["report"]["runs"][0]["results"] = []
        body["binding"]["sast_evidence_sha256"] = fingerprint(body["report"])
        run_id = str(uuid4())
        assert client.put(f"/api/platform/runs/{run_id}", json=body).json()["status"] == "skipped"


def test_http_cancel_waits_for_running_inspector_cleanup(tmp_path, monkeypatch):
    import threading
    from fastapi.testclient import TestClient
    import dast_agent.server as server

    started, cleaned = threading.Event(), threading.Event()

    async def inspector(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0.05)
            cleaned.set()

    monkeypatch.setattr(server, "orchestrate", inspector)
    monkeypatch.setenv("DAST_PLATFORM_TOKEN", "test-only-platform-token")
    monkeypatch.setenv("DAST_PLATFORM_TARGET_ID", "local-fixture")
    headers = {"Authorization": "Bearer test-only-platform-token"}
    run_id, body = str(uuid4()), payload()
    path = f"/api/platform/runs/{run_id}"
    with TestClient(server.create_app(runs_dir=tmp_path), base_url="http://127.0.0.1:8765", headers=headers) as client:
        assert client.put(path, json=body).status_code == 200
        assert started.wait(timeout=3)
        assert client.post(path + "/cancel").json()["stopped"]
        assert cleaned.is_set()
        assert client.get(path).json()["status"] == "cancelled"
        assert client.put(path, json=body).status_code == 409
    with TestClient(server.create_app(runs_dir=tmp_path), base_url="http://127.0.0.1:8765", headers=headers) as client:
        assert client.get(path).json()["status"] == "cancelled"
        assert client.put(path, json=body).status_code == 409
