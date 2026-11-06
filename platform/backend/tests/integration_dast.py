"""Cross-module contract: real platform/DB and DAST server; cloud/model boundaries replaced."""

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from test_dast import enqueue, runner
from test_sandbox import Ecs
from test_security import Sast, queued
from test_security import runner as sast_runner
from test_worker import reader

from delta_platform.worker.main import process_one


@pytest.mark.parametrize("browser_ready", [True, False], ids=["reproduced", "preflight-blocked"])
def test_platform_uses_actual_team_dast_api(store, data, monkeypatch, tmp_path, browser_ready):
    import dast_agent.server as server
    from dast_agent.registry import load_catalog

    sample = (
        Path(__file__).resolve().parents[3] / "services/delta-dast/examples/delta-sast-board.sarif"
    )
    report = json.loads(sample.read_text())
    catalog = load_catalog()
    catalog.targets["platform-sandbox"] = catalog.targets["local-fixture"].model_copy(
        # This contract test exercises inspection, not repository checkout or auto-fix models.
        update={"id": "platform-sandbox", "origin": "http://sandbox.test", "repo": None}
    )
    monkeypatch.setattr(server, "load_catalog", lambda: catalog)
    monkeypatch.setenv("DAST_PLATFORM_TOKEN", "test-only-dast-token")
    monkeypatch.setenv("DAST_PLATFORM_TARGET_ID", "platform-sandbox")
    inspected = []
    preflight = []
    feedback = []

    async def browser_available():
        preflight.append("browser")
        return browser_ready

    async def check_target(origin, entry_path, **kwargs):
        preflight.append((origin, entry_path))

    async def prioritize(findings, results):
        return {"summary": "fixture", "items": []}

    def sast_feedback(run_id, results):
        feedback.append((run_id, results))
        return []

    # Keep real preflight/routing active; replace only external credential/browser/network/model IO.
    monkeypatch.setattr(server, "_claude_auth", lambda: {"logged_in": True, "method": "test"})
    monkeypatch.setattr(server, "_codex_auth", lambda: {"logged_in": False, "method": "none"})
    monkeypatch.setattr(server, "browser_available", browser_available)
    monkeypatch.setattr(server, "check_target", check_target)
    monkeypatch.setattr(server, "prioritize", prioritize)
    monkeypatch.setattr(server.sast_link, "feedback", sast_feedback)

    async def inspect(
        finding, target, payload_id, template, run_id, output, publish, planner, impact, **kwargs
    ):
        inspected.append((finding.id, target.origin, planner, impact))
        return {
            "finding_id": finding.id,
            "status": "REPRODUCED",
            "message": "fixture",
            "evidence": {"num_turns": 1},
            "duration_ms": 1,
        }

    monkeypatch.setattr(server, "orchestrate", inspect)

    class ReportSource(Sast):
        def __call__(self, request):
            if request.url.path.endswith("/sarif"):
                return httpx.Response(200, json=report)
            return super().__call__(request)

    security, build = queued(store, data)
    source = ReportSource()
    process_one(store, reader(), security=sast_runner(source))
    check, _ = enqueue(store, build["service_id"], build["id"], security["id"])
    ecs = Ecs()
    with TestClient(
        server.create_app(runs_dir=tmp_path), base_url="http://127.0.0.1:8765"
    ) as client:

        def transport(request):
            response = client.request(
                request.method,
                request.url.path,
                content=request.content,
                headers={
                    k: request.headers[k]
                    for k in ("Authorization", "Content-Type")
                    if k in request.headers
                },
            )
            return httpx.Response(response.status_code, json=response.json())

        process_one(store, reader(), dast=runner(ecs, transport, source))
    result = store.run(check["id"])
    assert result["status"] == "succeeded", result.get("error")
    evidence = result["result"]
    assert preflight[0] == "browser"
    assert evidence["eligible_count"] > 0
    assert len(feedback) == 1 and feedback[0][0] == str(security["id"])
    assert evidence["deployment_approved"] is False and evidence["requires_review"] is True
    if browser_ready:
        assert evidence["dast_counts"]["REPRODUCED"] == len(inspected) == evidence["eligible_count"]
        assert preflight[1][0] == "http://sandbox.test"
    else:
        assert inspected == [] and preflight == ["browser"]
        assert evidence["dast_counts"]["REPRODUCED"] == 0
        assert evidence["dast_counts"]["INCONCLUSIVE"] == evidence["eligible_count"]
    assert result["result"]["execution_cloud"] == "aws"
    assert all(
        origin == "http://sandbox.test" and planner == "agent" and impact is True
        for _, origin, planner, impact in inspected
    )
    assert ecs.count == 0
    assert (tmp_path / str(check["id"]) / "report.json").is_file()
    assert json.loads((tmp_path / ".platform" / (str(check["id"]) + ".json")).read_text())[
        "cancelled"
    ]
