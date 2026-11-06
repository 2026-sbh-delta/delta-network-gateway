import json
from dataclasses import replace

import pytest

from delta_ai.cases import CaseReview, CaseStore, IncidentCase
from delta_ai.diagnostic_contract import DiagnosisRequest
from delta_pipeline.case_learning import CaseLearning, begin_attempt
from delta_pipeline.clients import ServiceUnavailable
from delta_pipeline.reports import ReportRef
from test_diagnostics import Diagnostic, make_run
from test_flow import _execute


class KnowledgeDiagnostic(Diagnostic):
    def __init__(self, store, *, down=False):
        super().__init__()
        self.store, self.down = store, down
    def publish_case(self, body):
        if self.down:
            raise ServiceUnavailable("token=NEVER-ECHO")
        case = IncidentCase.model_validate(body)
        return {"case_id": case.request.case_id, "revision": self.store.publish(case)}


def latest(store):
    return IncidentCase.model_validate_json(store.list()[0]["payload"])


def test_real_graph_fix_reverification_publishes_success_and_can_be_used_next_time(tmp_path):
    from delta_ai.diagnostics import diagnose
    store = CaseStore(tmp_path / "knowledge.sqlite")
    diagnostic = KnowledgeDiagnostic(store)
    run, build, deploy = make_run(tmp_path / "graph", diagnostic=diagnostic)
    final = _execute(run, ["fix"])
    assert final["outcome"] == "verified"
    case = latest(store)
    assert case.resolution.outcome == "success" and case.resolution.action == "code_fix"
    assert case.resolution.checks[0].check == "dast_wait"
    assert case.resolution.checks[0].reference != case.request.run_id
    assert json.loads(case.resolution.checks[0].details)["commit"] != case.request.context.commit
    assert len(build.keys) == len(deploy.keys) == 2
    request = DiagnosisRequest.model_validate({**case.request.model_dump(mode="json"),
        "case_id": "next-case", "run_id": "next-run", "observations": [{"evidence_id": "e2",
        "source": "dast", "summary": "다른 입력에서 XSS 재현"}]})
    assert not store.search(request)
    row = store.list()[0]
    store.review(case.request.case_id, row["revision"], CaseReview(decision="approved", reviewer="operator",
        reason="동일 finding 재검사 완료 확인", required_terms=("재검사",)))
    assert store.search(request)[0]["reference"]["case_id"] == case.request.case_id
    assert all(p.stat().st_mode & 0o077 == 0 for p in (tmp_path / "graph" / "case-outbox").iterdir())


def test_actual_reproduced_again_is_failed_remediation_and_human_approval_cannot_turn_it_into_success(tmp_path):
    from fakes import FakeDast
    store = CaseStore(tmp_path / "knowledge.sqlite")
    run, _, _ = make_run(tmp_path / "graph", diagnostic=KnowledgeDiagnostic(store))
    run._deps = replace(run._deps, dast=FakeDast(["REPRODUCED"], after_fix=["REPRODUCED"]))
    assert _execute(run, ["fix", "approve"])["outcome"] == "unresolved"
    cases = [IncidentCase.model_validate_json(r["payload"]) for r in store.list()]
    repaired = [c for c in cases if c.resolution.action == "code_fix"]
    assert len(repaired) == 1 and repaired[0].resolution.outcome == "failure"
    assert not any(c.resolution.outcome == "success" for c in cases)


def test_stop_is_not_success_and_server_outage_preserves_outbox_then_replays_idempotently(tmp_path):
    store = CaseStore(tmp_path / "knowledge.sqlite")
    diagnostic = KnowledgeDiagnostic(store, down=True)
    run, _, _ = make_run(tmp_path / "graph", diagnostic=diagnostic)
    assert _execute(run, ["stop"])["outcome"] == "stopped"
    assert not store.list()
    outbox = tmp_path / "graph" / "case-outbox"
    assert len(list(outbox.glob("*.json"))) == 2
    diagnostic.down = False
    CaseLearning(run._deps).flush()
    assert latest(store).resolution.outcome == "stopped"
    assert len(list(outbox.glob("*.ack"))) == 2
    # Remove ack to simulate a crash between server commit and local acknowledgement.
    next(outbox.glob("*.ack")).unlink()
    CaseLearning(run._deps).flush()
    assert len(store.list()) == 1 and latest(store).resolution.outcome == "stopped"


def test_acknowledged_history_cannot_starve_later_pending_cases(tmp_path):
    store = CaseStore(tmp_path / "knowledge.sqlite")
    diagnostic = KnowledgeDiagnostic(store, down=True)
    run, _, _ = make_run(tmp_path / "graph", diagnostic=diagnostic)
    _execute(run, ["stop"])
    outbox = tmp_path / "graph" / "case-outbox"
    for i in range(101):
        path = outbox / f"aaa-{i:04}.json"
        path.write_text("already sent")
        path.with_suffix(".ack").touch()
    diagnostic.down = False
    CaseLearning(run._deps).flush()
    assert latest(store).resolution.outcome == "stopped"


def test_pending_native_attempt_records_unknown_until_completed_runtime_check(tmp_path):
    store = CaseStore(tmp_path / "knowledge.sqlite")
    run, _, _ = make_run(tmp_path / "graph", diagnostic=KnowledgeDiagnostic(store))
    _execute(run, ["stop"])
    ref = next(n["detail"]["report"]["reference"] for n in run.snapshot()["nodes"] if n["id"] == "exception-report")
    pending = begin_attempt(run._deps, ref, action="retry_same_job", attempt=1, target="onprem_wait")
    assert latest(store).resolution.outcome == "unknown"
    learning = CaseLearning(run._deps)
    # Submission accepted is not completed execution.
    state = {"learning_pending": pending, "onprem_run_id": "native-1"}
    assert learning.settled("onprem_start", state, {}) == {}
    assert latest(store).resolution.outcome == "unknown"
    run._board.update("stage", state="pass")
    assert learning.settled("onprem_wait", state, {}) == {"learning_pending": None}
    case = latest(store)
    assert case.resolution.outcome == "success"
    assert case.resolution.checks[0].check == "runtime-binding-http-cleanup"


def test_learning_failure_never_changes_the_original_gate(tmp_path, monkeypatch):
    def fail(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(CaseLearning, "flush", fail)
    run, _, _ = make_run(tmp_path)
    assert _execute(run, ["stop"])["outcome"] == "stopped"
    assert list((tmp_path / "case-outbox").glob("*.json"))


def test_pending_attempt_on_cancel_is_stopped_and_never_a_failed_fix(tmp_path):
    store = CaseStore(tmp_path / "knowledge.sqlite")
    run, _, _ = make_run(tmp_path / "graph", diagnostic=KnowledgeDiagnostic(store))
    _execute(run, ["stop"])
    ref = next(n["detail"]["report"]["reference"] for n in run.snapshot()["nodes"] if n["id"] == "exception-report")
    pending = begin_attempt(run._deps, ref, action="retry_same_job", attempt=1, target="onprem_wait")
    deps = replace(run._deps, cancelled=lambda: True)
    CaseLearning(deps).settled("onprem_wait", {"learning_pending": pending}, {"halt": "cancelled"})
    assert latest(store).resolution.outcome == "stopped"


def test_graph_http_adapter_api_review_then_next_diagnosis_and_eval_export(tmp_path):
    import io
    from fastapi.testclient import TestClient
    from delta_ai.api import create_app
    from delta_ai.case_evaluation import export_candidate
    from delta_ai.diagnostics import FakeDiagnosticLLM
    from delta_ai.store import RuleStore
    from delta_pipeline.diagnostic_client import DiagnosticClient

    store = CaseStore(tmp_path / "knowledge.sqlite")
    api = TestClient(create_app(llm=None, diagnostic_llm=FakeDiagnosticLLM(),
        token="producer", store=RuleStore(tmp_path / "rules.json"), case_store=store))
    port = DiagnosticClient("http://127.0.0.1:8090", "producer")
    class Response(io.BytesIO):
        status = 200
    class InProcessHttp:
        def open(self, request, timeout):
            response = api.request(request.method, request.full_url.removeprefix("http://127.0.0.1:8090"),
                                   content=request.data, headers=dict(request.header_items()))
            assert response.status_code == 200, response.text
            return Response(response.content)
    port._opener = InProcessHttp()
    run, _, _ = make_run(tmp_path / "graph", diagnostic=port)
    assert _execute(run, ["fix"])["outcome"] == "verified"
    case = latest(store)
    assert case.resolution.outcome == "success"
    row = store.list()[0]
    store.review(case.request.case_id, row["revision"], CaseReview(decision="approved", reviewer="operator",
        reason="수정 커밋과 DAST 재검사 근거 확인", required_terms=("관측 사실",)))
    # Simulate another invocation, with new observations and incident IDs.
    request = {**case.request.model_dump(mode="json"), "case_id": "next-case", "run_id": "next-run",
               "observations": [{"evidence_id": "new-observation", "source": "dast", "summary": "다른 입력 XSS 재현"}]}
    report = port.diagnose(request)
    assert report["related_cases"][0]["case_id"] == case.request.case_id
    assert report["requires_human"] is True
    exported = export_candidate(CaseStore(tmp_path / "knowledge.sqlite"), case.request.case_id)
    assert exported.revision == row["revision"]


@pytest.mark.parametrize("point", ["human_wait", "manual_after_create", "manual_after_wait"])
def test_native_revalidation_survives_process_death_and_records_one_completed_attempt(tmp_path, point):
    import subprocess
    import sys
    from pathlib import Path
    helper = Path(__file__).with_name("recovery_killable.py")
    first = subprocess.run([sys.executable, str(helper), str(tmp_path), point], capture_output=True, text=True,
                           timeout=20, check=False)
    assert first.returncode == 9, first.stderr
    done = subprocess.run([sys.executable, str(helper), str(tmp_path), "human_resume"], capture_output=True,
                          text=True, timeout=20, check=False)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout.splitlines()[-1])["outcome"] == "verified"
    cases = [IncidentCase.model_validate_json(p.read_bytes()) for p in (tmp_path / "graph" / "case-outbox").glob("*.json")]
    resolved = [c for c in cases if c.resolution.outcome == "success"]
    assert len(resolved) == 1 and resolved[0].resolution.action == "redeploy_sandbox"
    assert len(json.loads((tmp_path / "jobs.json").read_text())) == 4
