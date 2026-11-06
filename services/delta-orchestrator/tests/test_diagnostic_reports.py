"""Authenticated report access survives later diagnoses and fix-round history."""

import json

from delta_ai.diagnostic_contract import DiagnosisRequest, unavailable_report
from delta_orchestrator.app import create_app
from delta_orchestrator.pipelines import Pipelines
from delta_pipeline.reports import ReportStore
from fastapi.testclient import TestClient


def test_previous_gate_and_round_reports_remain_accessible_only_in_their_run(
    monkeypatch, tmp_path
):
    origin = "https://orchestrator.example"
    monkeypatch.setenv("ORCH_PUBLIC_ORIGIN", origin)
    monkeypatch.setenv("ORCH_ADMIN_PASSWORD", "test-orchestrator-password")
    monkeypatch.setenv("ORCH_SESSION_SECRET", "s" * 40)
    pipelines = Pipelines(tmp_path)
    refs = []
    for case in ("mapping", "first-dast", "second-dast"):
        request = DiagnosisRequest(
            case_id=case,
            run_id="run1",
            service_id="web",
            node_id="dast",
            trigger="ambiguous_result",
            observations=[
                {"evidence_id": "fact", "source": "dast", "summary": "INCONCLUSIVE"}
            ],
        )
        refs.append(
            ReportStore(tmp_path / "reports")
            .save(request, unavailable_report(request))
            .model_dump()
        )
    board = {
        "nodes": [
            {"detail": {"diagnostic_report": {"reference": refs[0]}}},
            {
                "detail": {},
                "history": [{"detail": {"diagnostic_report": {"reference": refs[1]}}}],
            },
            {"detail": {"report": {"reference": refs[2]}}},
        ]
    }
    (tmp_path / "runs").mkdir()
    for run in ("run1", "run2"):
        (tmp_path / "runs" / (run + ".json")).write_text(json.dumps(board))
    with TestClient(create_app(pipelines), base_url=origin) as client:
        path = "/api/pipeline/runs/run1/reports/"
        assert client.get(path + "mapping/json").status_code == 401
        assert (
            client.post(
                "/login",
                data={"password": "test-orchestrator-password"},
                headers={"Origin": origin},
                follow_redirects=False,
            ).status_code
            == 303
        )
        for ref in refs:
            for format in ("json", "md"):
                assert (
                    client.get(path + ref["case_id"] + "/" + format).status_code == 200
                )
        assert (
            client.get(path.replace("run1", "run2") + "mapping/json").status_code == 404
        )
        assert client.get(path + "unknown/json").status_code == 404
        (tmp_path / "reports/run1/mapping/report.json").write_text("tampered")
        assert client.get(path + "mapping/json").status_code == 404
