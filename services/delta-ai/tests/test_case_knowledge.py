import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from delta_ai.api import create_app
from delta_ai.case_evaluation import EvaluationDataset, evaluate, export_candidate
from delta_ai.cases import (CaseConflict, CaseReview, CaseStore, IncidentCase, Resolution,
                            VerificationCheck, digest)
from delta_ai.diagnostic_contract import (DiagnosisRequest, diagnosis_hash, unavailable_report)
from delta_ai.diagnostics import diagnose
from delta_ai.llm import LlmResponse
from delta_ai.store import RuleStore


def incident(case_id="case-old", run_id="run-old", symptom="HTTP timeout", outcome="success", sequence=0,
             service="notes", environment="onprem", error="network_timeout"):
    request = DiagnosisRequest(case_id=case_id, run_id=run_id, service_id=service, node_id="onprem_wait",
        trigger="execution_failure", context={"environment": environment, "error_code": error},
        observations=[{"evidence_id": "event1", "source": "runtime", "summary": symptom}])
    report = unavailable_report(request).model_copy(update={"status": "analyzed"})
    checks = () if outcome in ("unknown", "stopped") else (VerificationCheck(check="runtime-binding-http-cleanup",
        status="pass" if outcome == "success" else "fail", reference="native-old", sha256="a" * 64),)
    return IncidentCase(request=request, report=report, report_sha256="b" * 64, sequence=sequence,
        resolution=Resolution(outcome=outcome, action="retry_same_job" if checks else "none",
                              scope="onprem_wait", checks=checks))


def approved(store, case, **kwargs):
    revision = store.publish(case)
    store.review(case.request.case_id, revision, CaseReview(decision="approved", reviewer="operator",
        reason="동일 작업 조회와 실제 HTTP 재검사 근거를 확인함. 원인 확정은 아님.",
        required_terms=("HTTP",), forbidden_terms=("원인 확정",), **kwargs))
    return revision


def query(**kwargs):
    return incident(case_id="new", run_id="new-run", symptom="HTTP result missing", **kwargs).request


def test_persistence_review_and_version_isolation(tmp_path):
    path = tmp_path / "cases.sqlite"
    store = CaseStore(path)
    case = incident()
    revision = store.publish(case)
    assert store.publish(case) == revision
    assert not store.search(query())
    approved(store, case)
    assert CaseStore(path).search(query())[0]["reference"]["revision"] == revision
    failed = incident(outcome="failure", sequence=1)
    store.publish(failed)
    assert not store.search(query())  # a changed result cannot inherit approval
    with pytest.raises(CaseConflict):
        store.review(case.request.case_id, revision, CaseReview(decision="approved", reviewer="operator", reason="stale"))
    assert store.publish(case) == revision  # stale replay cannot regress the latest outcome
    assert store.list()[0]["sequence"] == 1
    assert path.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("outcome", ["unknown", "stopped"])
def test_acknowledgement_or_stop_cannot_be_promoted(tmp_path, outcome):
    store = CaseStore(tmp_path / "cases.sqlite")
    case = incident(outcome=outcome)
    revision = store.publish(case)
    with pytest.raises(CaseConflict):
        store.review(case.request.case_id, revision, CaseReview(decision="approved", reviewer="operator", reason="clicked"))


def test_success_requires_completed_pass_and_failure_requires_failed_check():
    with pytest.raises(ValueError):
        Resolution(outcome="success", scope="wait", action="retry_same_job")
    with pytest.raises(ValueError):
        Resolution(outcome="failure", scope="wait", action="retry_same_job",
                   checks=[VerificationCheck(check="http", status="unknown", reference="r", sha256="a" * 64)])


def test_immutable_binding_and_concurrent_publication(tmp_path):
    store = CaseStore(tmp_path / "cases.sqlite")
    case = incident()
    with ThreadPoolExecutor(max_workers=4) as pool:
        revisions = list(pool.map(lambda _: store.publish(case), range(8)))
    assert len(set(revisions)) == len(store.list()) == 1
    changed = incident(sequence=1, symptom="different input")
    with pytest.raises(CaseConflict):
        store.publish(changed)


def test_approval_can_be_withdrawn_without_erasing_review_history(tmp_path):
    store = CaseStore(tmp_path / "cases.sqlite")
    case = incident()
    revision = approved(store, case)
    assert store.search(query())
    store.review(case.request.case_id, revision, CaseReview(decision="rejected", reviewer="operator",
                                                          reason="추가 확인에서 오판 발견"))
    assert not store.search(query())
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM case_review_events").fetchone()[0] == 2


def test_search_is_scoped_bounded_and_excludes_self_duplicates_and_unrelated_codes(tmp_path):
    store = CaseStore(tmp_path / "cases.sqlite")
    for i in range(5):
        approved(store, incident(case_id=f"c{i}", run_id=f"r{i}", symptom=f"past timeout {i}"))
    approved(store, incident(case_id="foreign", service="another-service"))
    approved(store, incident(case_id="gcp", environment="gcp"))
    approved(store, incident(case_id="unrelated", error="database_error"))
    assert [c["reference"]["case_id"] for c in store.search(query())] == ["c0", "c1", "c2"]
    assert all(c["reference"]["case_id"] != "c0" for c in store.search(
        incident(case_id="changed-id", run_id="different", symptom="past timeout 0").request))
    assert all(c["reference"]["run_id"] != "r0" for c in store.search(query().model_copy(update={"run_id": "r0"})))
    assert not store.search(query().model_copy(update={"context": None}))


class Analysis:
    model = "case-test"
    def __init__(self, *, bad=False):
        self.requests = []
        self.bad = bad
    async def complete(self, prompt):
        self.requests.append(prompt)
        payload = json.loads(prompt.user.split("\n", 1)[1].rsplit("\n", 1)[0])
        cases = payload["historical_cases"]
        draft = {"summary": "HTTP 결과와 실행 바인딩을 확인한다.",
            "hypotheses": [{"explanation": "HTTP 조회 연결을 확인할 필요가 있다.", "confidence": "low",
                "evidence_ids": ["event1"], "historical_case_ids": ["invented"] if self.bad else
                    [c["reference"]["case_id"] for c in cases]}],
            "next_checks": ["HTTP 상태 확인"], "limitations": ["원인은 미확정"]}
        return LlmResponse(json.dumps(draft), self.model)


async def test_diagnosis_uses_reviewed_history_with_traceable_snapshot_and_rejects_invented_citations(tmp_path):
    store = CaseStore(tmp_path / "cases.sqlite")
    llm = Analysis()
    before = await diagnose(query(), llm, case_store=store)
    revision = approved(store, incident())
    after = await diagnose(query(), llm, case_store=store)
    assert before.input_hash == after.input_hash == diagnosis_hash(query())
    assert before.knowledge_snapshot_hash != after.knowledge_snapshot_hash
    assert llm.requests[0].input_hash != llm.requests[1].input_hash
    assert after.related_cases[0].revision == revision
    assert after.hypotheses[0].historical_case_ids == ("case-old",)
    assert "현재 원인 확정" in " ".join(after.limitations)
    assert (await diagnose(query(), Analysis(bad=True), case_store=store)).status == "invalid_analysis"


async def test_case_store_outage_preserves_original_analysis_and_explicit_limitation():
    class BrokenStore:
        def search(self, request):
            raise OSError("token=DO-NOT-EXPORT")
    report = await diagnose(query(), Analysis(), case_store=BrokenStore())
    assert report.status == "analyzed" and not report.related_cases
    assert any("저장소를 조회하지 못했다" in note for note in report.limitations)
    assert "DO-NOT-EXPORT" not in report.model_dump_json()


def test_publishing_api_auth_and_producer_cannot_review(tmp_path):
    store = CaseStore(tmp_path / "cases.sqlite")
    client = TestClient(create_app(llm=None, store=RuleStore(tmp_path / "rules.json"), token="producer",
                                    case_store=store))
    body = incident().model_dump(mode="json")
    assert client.post("/v1/incident-cases", json=body).status_code == 401
    auth = {"Authorization": "Bearer producer"}
    response = client.post("/v1/incident-cases", json=body, headers=auth)
    assert response.status_code == 200 and response.json()["review"] == "required"
    assert not store.search(query())
    assert client.post("/v1/incident-cases/case-old/review", json={}, headers=auth).status_code == 404
    body["report"]["unexpected"] = "token=NEVER-ECHO"
    response = client.post("/v1/incident-cases", json=body, headers=auth)
    assert response.status_code == 422 and "NEVER-ECHO" not in response.text
    assert client.post("/v1/incident-cases", content="x" * 64001, headers=auth).status_code == 413


async def test_export_requires_independent_labels_and_evaluation_excludes_all_held_out_cases(tmp_path):
    store = CaseStore(tmp_path / "cases.sqlite")
    case = incident()
    approved(store, case)
    held_out = export_candidate(store, case.request.case_id)
    approved(store, incident(case_id="same-symptoms", run_id="other-run"))
    approved(store, incident(case_id="reference", run_id="reference-run", symptom="different timeout"))
    dataset = EvaluationDataset(candidates=(held_out,))
    report = await evaluate(dataset, llm=Analysis(), store=store)
    assert report["baseline_passed"] == report["with_cases_passed"] == 1
    assert report["cases"][0]["baseline"]["retrieved_case_ids"] == []
    assert report["cases"][0]["with_cases"]["retrieved_case_ids"] == ["reference"]
    no_labels = incident(case_id="no-labels", symptom="no labels")
    rev = store.publish(no_labels)
    store.review("no-labels", rev, CaseReview(decision="approved", reviewer="operator", reason="verified"))
    with pytest.raises(ValueError):
        export_candidate(store, "no-labels")


async def test_evaluation_snapshot_is_frozen_before_llm_calls(tmp_path):
    store = CaseStore(tmp_path / "cases.sqlite")
    approved(store, incident())
    dataset = EvaluationDataset(candidates=(export_candidate(store, "case-old"),))
    class MutatingAnalysis(Analysis):
        async def complete(self, prompt):
            if not self.requests:
                approved(store, incident(case_id="created-mid-eval", symptom="new different evidence"))
            return await super().complete(prompt)
    result = await evaluate(dataset, llm=MutatingAnalysis(), store=store)
    assert result["cases"][0]["with_cases"]["retrieved_case_ids"] == []


def test_case_and_review_secrets_are_redacted_before_persistence(tmp_path):
    store = CaseStore(tmp_path / "cases.sqlite")
    case = incident(symptom="password=NEVER-STORE Bearer NEVER-TOKEN")
    store.publish(case)
    assert "NEVER-STORE" not in store.list()[0]["payload"]
    assert "NEVER-TOKEN" not in store.list()[0]["payload"]


def test_cli_review_export_and_no_overwrite(tmp_path, capsys):
    from delta_ai.case_cli import main
    path = tmp_path / "cases.sqlite"
    store = CaseStore(path)
    case = incident()
    rev = store.publish(case)
    review = tmp_path / "review.json"
    review.write_text(json.dumps({"decision": "approved", "reviewer": "operator", "reason": "HTTP evidence",
                                "required_terms": ["HTTP"]}))
    assert main(["--store", str(path), "review", "case-old", rev, "--file", str(review)]) == 0
    export = tmp_path / "candidate.json"
    assert main(["--store", str(path), "export", "case-old", "--output", str(export)]) == 0
    assert export.stat().st_mode & 0o077 == 0
    assert EvaluationDataset.model_validate_json(export.read_bytes()).candidates[0].revision == rev
    with pytest.raises(FileExistsError):
        main(["--store", str(path), "export", "case-old", "--output", str(export)])
