import sqlite3
import threading
import time
from copy import deepcopy
from io import BytesIO

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from delta_pipeline.clients import ServiceUnavailable
from delta_pipeline.diff_client import DiffClient, DiffInput, DiffJob, DiffResult
from delta_pipeline.graph import build_graph
from delta_pipeline.model import Options
from delta_pipeline.run import CHECKPOINT_FILE, Run
from delivery_fakes import (
    TARGET, TEST_RELEASE_CONFIG, TEST_RELEASE_SIGNING_KEY, RoundJenkins,
    bundle, release_result, stage_result,
)
from fakes import COMMIT, FIX_COMMIT, IMAGE_REPO, REPO, FakeDast, FakeSast, FakeStaging
from test_cutover import CFG, Clock, FakeCloudflare, FakeInternet
from test_diagnostics import Diagnostic

INPUT = DiffInput.model_validate({"service_id": "delta-sample-app",
    "source": {"state": {"bucket": "demo-state", "key": "workload/terraform.tfstate"}},
    "target": {"region": "asia-northeast3"}})
RUN_ID = "e" * 32


def response(run_id, verdict):
    grade = {"pass": "exact", "needs_human": "gap", "fix": "approximate"}[verdict]
    return DiffResult.model_validate({"diff_id": run_id, "source": {**INPUT.source.state.model_dump(), "serial": 21},
        "assessment": {"assessment_id": run_id, "kind": "migration_mapping", "verdict": verdict,
                       "mapping": {"region": "asia-northeast3", "network_mode": "create", "rows": [
                           {"resource": "subnet", "source": "workload", "target": "gcp-workload", "grade": grade,
                            "value_source": "terraform", "rule_id": "MAP-NET-001"}]},
                       "checks": [{"rule_id": "MAP-NET-001", "name": "subnet", "state": "green" if verdict == "pass" else "red"}]}})


class Diff:
    def __init__(self, verdict, wait_for=None):
        self.verdict, self.wait_for = verdict, wait_for
        self.calls = []
        self.started = threading.Event()

    def compare(self, config, run_id):
        self.calls.append((config, run_id))
        self.started.set()
        if self.wait_for is not None and not self.wait_for.wait(4):
            raise ServiceUnavailable("DAST did not start while Diff was running")
        if self.verdict == "failure":
            raise ServiceUnavailable("Diff API HTTP 503")
        return response(run_id, self.verdict)


def make(tmp_path, diff, *, diagnostic=None, dast=None, sast=None, cf=None, config=INPUT, options=None):
    clock, cf = Clock(), cf or FakeCloudflare()
    net = FakeInternet(cf)
    release = RoundJenkins(lambda commit: release_result(bundle(commit)))
    run = Run(options or Options(REPO, COMMIT, cutover=True, release_target=TARGET, diff_request_hash=INPUT.fingerprint),
              sast=sast or FakeSast(), dast=dast or FakeDast(), build_jenkins=RoundJenkins(bundle),
              deploy_jenkins=RoundJenkins(lambda commit: stage_result(bundle(commit))),
              staging=FakeStaging(), image_repo=IMAGE_REPO, store_dir=tmp_path, run_id=RUN_ID,
              sleep=clock.sleep, now=clock.now, cloudflare=cf, cutover=CFG, probe=net.probe, resolve=net.resolve,
              release_jenkins=release, release_signing_key=TEST_RELEASE_SIGNING_KEY,
              release_config=deepcopy(TEST_RELEASE_CONFIG), diagnostic=diagnostic or Diagnostic(),
              diff=DiffJob(diff, config, RUN_ID) if diff is not None else None, require_diff_cutover=True)
    return run, release, cf


def run_with_answers(run, answers, inspect=None):
    thread = threading.Thread(target=run.execute, daemon=True)
    thread.start()
    order = []
    pending = dict(answers)
    deadline = time.monotonic() + 8
    while thread.is_alive() and time.monotonic() < deadline:
        decision = next((n for n in run.snapshot()["nodes"] if n.get("decision")), None)
        if decision and decision["id"] in pending:
            if inspect:
                inspect(decision)
            if run.answer(pending[decision["id"]], decision_id=decision["decision"]["id"]):
                order.append(decision["id"])
                del pending[decision["id"]]
        thread.join(0.01)
    assert not thread.is_alive()
    assert not pending
    return run.snapshot(), order


def test_diff_overlaps_through_dast_and_approval_precedes_release_and_dns(tmp_path):
    reached_dast = threading.Event()
    diff = Diff("needs_human", wait_for=reached_dast)

    class Dast(FakeDast):
        def submit(self, *args, **kwargs):
            assert diff.started.wait(1) and not reached_dast.is_set()
            reached_dast.set()
            return super().submit(*args, **kwargs)

    run, release, cf = make(tmp_path, diff, dast=Dast())
    def inspect(node):
        assert not cf.writes
        if node["id"] == "map":
            assert not release.keys
            assert not run.answer("approve")
            assert not run.answer("approve", decision_id="stale-approval")
            assert node["detail"]["verdict"] == "needs_human"

    final, order = run_with_answers(run, {"map": "approve", "release": "approve", "cutover": "approve"}, inspect)
    assert order == ["map", "release", "cutover"]
    assert final["outcome"] == "verified" and cf.writes == [CFG.blue]
    node = next(n for n in final["nodes"] if n["id"] == "map")
    assert (node["state"], node["detail"]["verdict"], node["detail"]["review"]) == ("warn", "needs_human", "approved")
    assert diff.calls == [(INPUT, RUN_ID)]


@pytest.mark.parametrize("verdict", ["failure", "fix"])
def test_failed_diff_goes_to_diagnosis_and_never_promotes(tmp_path, verdict):
    diagnostic = Diagnostic()
    run, release, cf = make(tmp_path, Diff(verdict), diagnostic=diagnostic)
    final, order = run_with_answers(run, {"exception-review": "acknowledge"})
    assert order == ["exception-review"]
    assert final["outcome"] == "stopped" and not release.keys and not cf.writes
    assert len(diagnostic.calls) == 1 and diagnostic.calls[0]["node_id"] == "infrastructure_diff"
    assert any(n["id"] == "exception-report" and n["state"] == "pass" for n in final["nodes"])


def test_web_failure_report_keeps_the_parallel_diff_failure(tmp_path):
    class Sast(FakeSast):
        def start(self, *args):
            raise ServiceUnavailable("SAST API HTTP 422")

    diagnostic = Diagnostic()
    run, release, cf = make(tmp_path, Diff("failure"), sast=Sast(), diagnostic=diagnostic)
    final, _ = run_with_answers(run, {"exception-review": "acknowledge"})
    assert final["outcome"] == "stopped" and not release.keys and not cf.writes
    assert len(diagnostic.calls) == 1
    assert any(o["evidence_id"] == "parallel-diff" and "HTTP 503" in o["summary"]
               for o in diagnostic.calls[0]["observations"])


def test_diff_review_rejection_and_legacy_cutover_cannot_bypass(tmp_path):
    run, release, cf = make(tmp_path, Diff("needs_human"))
    final, order = run_with_answers(run, {"map": "stop"})
    assert order == ["map"] and final["outcome"] == "stopped" and not release.keys and not cf.writes
    legacy = tmp_path / "legacy"
    old, release, cf = make(legacy, None, options=Options(REPO, COMMIT, cutover=True, release_target=TARGET))
    final, order = run_with_answers(old, {})
    assert not order
    assert final["outcome"] == "stopped" and not release.keys and not cf.writes


def test_pass_keeps_the_web_fix_loop_and_does_not_repeat_diff(tmp_path):
    diff, dast = Diff("pass"), FakeDast(["REPRODUCED"])
    options = Options(REPO, COMMIT, branch="feat/flask-board", cutover=True, release_target=TARGET,
                      diff_request_hash=INPUT.fingerprint)
    run, release, cf = make(tmp_path, diff, dast=dast, options=options)
    final, order = run_with_answers(run, {"dast": "fix", "release": "approve", "cutover": "approve"})
    assert order == ["dast", "release", "cutover"] and final["outcome"] == "verified"
    assert len(dast.prs) == 1 and len(dast.reverify) == 1
    assert list(dast.bindings.values())[-1]["commit_sha"] == FIX_COMMIT
    assert diff.calls == [(INPUT, RUN_ID)] and len(release.keys) == 1 and cf.writes == [CFG.blue]


def test_restart_reuses_diff_result_and_waits_for_a_new_decision_id(tmp_path):
    original = Diff("needs_human")
    first, _, cf = make(tmp_path, original)
    connection = sqlite3.connect(tmp_path / CHECKPOINT_FILE, check_same_thread=False)
    try:
        graph = build_graph(first._deps, SqliteSaver(connection))
        config = {"configurable": {"thread_id": RUN_ID}}
        graph.invoke({}, config, durability="sync")
        assert graph.get_state(config).next == ("ask_mapping",)
        assert not cf.writes and original.calls == [(INPUT, RUN_ID)]
    finally:
        connection.close()
    after_restart = Diff("failure")
    resumed, _, cf = make(tmp_path, after_restart, cf=cf)
    final, order = run_with_answers(resumed, {"map": "approve", "release": "approve", "cutover": "approve"})
    assert order == ["map", "release", "cutover"]
    assert final["outcome"] == "verified" and cf.writes == [CFG.blue] and after_restart.calls == []


def test_http_contract_authenticates_and_rejects_a_result_for_another_run(monkeypatch):
    with pytest.raises(ValueError):
        DiffClient("http://diff.cp.internal:8095", "fake-token")
    with pytest.raises(ValueError):
        DiffClient("http://external.invalid:8095", "fake-token", allow_private_http=True)
    client = DiffClient("http://diff.cp.internal:8095", "fake-token", allow_private_http=True)
    returned = response(RUN_ID, "needs_human")

    def send(request, timeout):
        assert request.full_url == "http://diff.cp.internal:8095/v1/diffs"
        assert request.get_method() == "POST" and request.get_header("Authorization") == "Bearer fake-token"
        assert b'"diff_id": "' + RUN_ID.encode() + b'"' in request.data
        return BytesIO(returned.model_dump_json().encode())

    monkeypatch.setattr(client._opener, "open", send)
    assert client.compare(INPUT, RUN_ID).assessment.verdict == "needs_human"
    returned = response("different-run", "pass")
    with pytest.raises(ServiceUnavailable, match="응답 검증 실패"):
        client.compare(INPUT, RUN_ID)
