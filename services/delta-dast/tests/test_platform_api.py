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
        assert calls[0][0].auto_fix is False and calls[0][0].impact is True
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

    async def ready(*args, **kwargs):
        return True

    # 사전 확인(LLM 인증·브라우저·대상 응답)은 통과시키고 취소 동작만 본다
    monkeypatch.setattr(server, "_claude_auth", lambda: {"logged_in": True, "method": "test"})
    monkeypatch.setattr(server, "browser_available", ready)
    monkeypatch.setattr(server, "check_target", ready)
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


def test_gcp_defaults_to_the_persistent_staging_target(tmp_path, monkeypatch):
    """GCP 는 실행마다 Cloud Run 을 만들지 않고 상시 스테이징(staging-gcp)을 점검한다. 주소는 스테이징 도구에서 받는다."""
    from dast_agent import staging_link

    registry, live, calls = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(staging_link, "origin", lambda cloud, timeout=15: "http://136.0.0.1")
    body = payload()
    body["binding"].update(execution_cloud="gcp", target_url="http://136.0.0.1", build_id="r1-build",
                           sast_run_id="r1-sast", runtime_revision="delta-lab-staging-00002-abc")
    run_id = str(uuid4())
    result = asyncio.run(registry.submit(run_id, body))
    assert result["status"] == "running"
    assert calls[0][0].target_id == "staging-gcp" and calls[0][0].auto_fix is False
    # 스테이징 도구가 주는 주소와 다른 곳은 점검하지 않는다
    other = payload()
    other["binding"].update(execution_cloud="gcp", target_url="http://203.0.113.9")
    with pytest.raises(ValueError):
        asyncio.run(registry.submit(str(uuid4()), other))


def test_platform_limit_checks_top_candidates_and_reports_the_rest_as_truncated(tmp_path, monkeypatch):
    registry, live, calls = fixture(tmp_path, monkeypatch)
    monkeypatch.setenv("DAST_PLATFORM_MAX_FINDINGS", "1")
    run_id = str(uuid4())
    view = asyncio.run(registry.submit(run_id, payload()))
    # 저장형·확정 후보 1건만 점검하고, 나머지는 점검하지 않았다고 남긴다(통과가 아니다)
    assert len(calls[0][0].report["findings"]) == 1 and len(view["finding_ids"]) == 1
    assert view["eligible_count"] == 3 and view["findings_truncated"] is True


def test_platform_run_replies_to_the_sast_scan_that_produced_it(tmp_path, monkeypatch):
    """플랫폼 경로도 점검이 끝나면 binding 의 SAST 점검 ID 로 재현 결과를 회신한다(통합하며 빠졌던 연결)."""
    import time
    from fastapi.testclient import TestClient
    import dast_agent.server as server
    from dast_agent import sast_link

    async def inspector(finding, *args, **kwargs):
        return {"finding_id": finding.id, "title": finding.title, "status": "REPRODUCED",
                "message": "실행 관측", "evidence": {}, "duration_ms": 1}

    async def ready(*args, **kwargs):
        return True

    replies = []

    def feedback(sast_run_id, results):
        replies.append((sast_run_id, [r["finding_id"] for r in results]))
        return [{"fingerprint": "2d3b1d57b297b50cf62704e6cb34a5be", "outcome": "ineffective", "detail": ""}]

    monkeypatch.setattr(server, "orchestrate", inspector)
    monkeypatch.setattr(server, "_claude_auth", lambda: {"logged_in": True, "method": "test"})
    monkeypatch.setattr(server, "browser_available", ready)
    monkeypatch.setattr(server, "check_target", ready)
    monkeypatch.setattr(sast_link, "feedback", feedback)
    monkeypatch.setenv("DAST_PLATFORM_TOKEN", "test-only-platform-token")
    monkeypatch.setenv("DAST_PLATFORM_TARGET_ID", "local-fixture")
    monkeypatch.setenv("DAST_PLATFORM_MAX_FINDINGS", "1")
    run_id, body = str(uuid4()), payload()
    with TestClient(server.create_app(runs_dir=tmp_path), base_url="http://127.0.0.1:8765",
                    headers={"Authorization": "Bearer test-only-platform-token"}) as client:
        assert client.put(f"/api/platform/runs/{run_id}", json=body).status_code == 200
        for _ in range(100):
            if client.get(f"/api/platform/runs/{run_id}").json()["status"] == "done":
                break
            time.sleep(0.05)
        view = client.get(f"/api/runs/{run_id}/view").json()
    assert replies == [(body["binding"]["sast_run_id"], ["sast-2d3b1d57b297b50cf62704e6cb34a5be"])]
    assert view["sast_feedback"][0]["outcome"] == "ineffective"


def test_demo_prefers_the_pinned_path_within_the_same_grade(tmp_path, monkeypatch):
    """시연 대상은 SARIF 순서가 아니라 지문으로 고정한다. 같은 등급(저장형·확정) 안에서만 앞당기고, 등급은 넘지 않는다."""
    registry, live, calls = fixture(tmp_path, monkeypatch)
    monkeypatch.setenv("DAST_PLATFORM_MAX_FINDINGS", "1")
    body = payload()
    results = body["report"]["runs"][0]["results"]
    from dast_agent.adapters import sarif_targets
    stored = [r for r in sarif_targets(body["report"]) if r["ruleId"] == "delta.xss.stored"]
    results.reverse()  # SARIF 순서가 바뀌어도
    body["binding"]["sast_evidence_sha256"] = fingerprint(body["report"])
    for wanted in (stored[0], stored[1]):
        fp = wanted["partialFingerprints"]["deltaFlow/v1"]
        monkeypatch.setenv("DAST_PLATFORM_PREFER", fp)
        calls.clear()
        asyncio.run(registry.submit(str(uuid4()), body))
        assert [f["id"] for f in calls[0][0].report["findings"]] == ["sast-" + fp]
    # 반사형(낮은 등급)을 지정해도 저장형보다 앞서지 않는다
    reflected = next(r for r in sarif_targets(body["report"]) if r["ruleId"] == "delta.xss.reflected")
    monkeypatch.setenv("DAST_PLATFORM_PREFER", reflected["partialFingerprints"]["deltaFlow/v1"])
    calls.clear()
    asyncio.run(registry.submit(str(uuid4()), body))
    assert calls[0][0].report["findings"][0]["id"] != "sast-" + reflected["partialFingerprints"]["deltaFlow/v1"]


def test_platform_runs_use_direct_check_unless_turned_off(tmp_path, monkeypatch):
    """파이프라인 경로는 기본으로 바로 검사(반사 확인·새 세션 재방문 생략)를 쓴다. 0 이면 기존 절차."""
    registry, live, calls = fixture(tmp_path, monkeypatch)
    asyncio.run(registry.submit(str(uuid4()), payload()))
    assert calls[0][1]["direct"] is True
    monkeypatch.setenv("DAST_PLATFORM_DIRECT", "0")
    asyncio.run(registry.submit(str(uuid4()), payload()))
    assert calls[1][1]["direct"] is False
