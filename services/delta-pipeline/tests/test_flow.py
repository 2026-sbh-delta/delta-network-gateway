from __future__ import annotations

import json
import threading
import time

import pytest
from fakes import FakeDast, FakeDelta, FakeJenkins

from delta_pipeline.clients import ServiceUnavailable
from delta_pipeline.graph import route_dast_judge
from delta_pipeline.model import SCENARIOS, Board, Options, combined_verdict, dast_request
from delta_pipeline.run import Run
from delta_pipeline.steps import READ_ATTEMPTS, Deps, Steps, Stopped, assert_gates_passed, sync_stages

SCENARIO = {s.id: s for s in SCENARIOS}


@pytest.fixture
def onprem_dir(tmp_path):
    for kind in ("runtime_security", "migration_parity"):
        (tmp_path / f"{kind}.request.json").write_text(json.dumps({"kind": kind, "evidence": {}}), encoding="utf-8")
    return tmp_path


def _run(scenario, onprem_dir, *, delta=None, dast=None, jenkins=None, ready=True):
    return Run(Options(SCENARIO[scenario]), delta=delta or FakeDelta(), dast=dast or FakeDast(), jenkins=jenkins or FakeJenkins(),
               onprem_dir=onprem_dir, store_dir=onprem_dir / "store", service_ready=lambda: ready, sleep=lambda _: None)


def _execute(run, answers=()):
    """실행하면서 사람 확인이 뜨면 정해 둔 답을 차례로 준다."""
    thread = threading.Thread(target=run.execute)
    thread.start()
    pending = list(answers)
    deadline = time.time() + 10
    while thread.is_alive() and time.time() < deadline:
        if pending and any(n["decision"] for n in run.snapshot()["nodes"]) and run.answer(pending[0]):
            pending.pop(0)
            time.sleep(0.05)
        time.sleep(0.01)
    thread.join(timeout=2)
    assert not thread.is_alive()
    return run.snapshot()


def _states(snapshot):
    return {n["id"]: n["state"] for n in snapshot["nodes"]}


def test_clean_release_runs_every_stage_and_deploys(onprem_dir):
    jenkins = FakeJenkins()

    snapshot = _execute(_run("normal", onprem_dir, jenkins=jenkins))

    assert snapshot["outcome"] == "deployed"
    assert jenkins.triggered == ["good"]
    assert _states(snapshot)["canary-100"] == "pass"


def test_vulnerable_release_never_reaches_jenkins_when_human_stops(onprem_dir):
    jenkins = FakeJenkins()

    snapshot = _execute(_run("vulnerable", onprem_dir, jenkins=jenkins), ["stop"])

    assert snapshot["outcome"] == "stopped"
    assert jenkins.triggered == []
    assert _states(snapshot)["deploy-prepare"] == "skipped"


def test_vulnerable_release_deploys_only_after_the_fixed_build_passes(onprem_dir):
    dast, jenkins = FakeDast(), FakeJenkins()

    snapshot = _execute(_run("vulnerable", onprem_dir, dast=dast, jenkins=jenkins), ["reverify"])

    assert dast.scenarios == ["vulnerable", "fixed"]
    assert snapshot["outcome"] == "deployed"
    assert jenkins.triggered == ["good"]


def test_onprem_finding_waits_for_a_person_before_dast(onprem_dir):
    dast = FakeDast()

    snapshot = _execute(_run("normal", onprem_dir, delta=FakeDelta(onprem="needs_human"), dast=dast), ["stop"])

    assert snapshot["outcome"] == "stopped"
    assert dast.scenarios == []


def test_judgment_outage_stops_without_deploying(onprem_dir):
    jenkins = FakeJenkins()

    snapshot = _execute(_run("normal", onprem_dir, delta=FakeDelta(fail=True), jenkins=jenkins))

    assert snapshot["outcome"] == "stopped"
    assert jenkins.triggered == []


def test_missing_onprem_evidence_stops(tmp_path):
    jenkins = FakeJenkins()

    snapshot = _execute(_run("normal", tmp_path, jenkins=jenkins))

    assert snapshot["outcome"] == "stopped"
    assert jenkins.triggered == []


def test_failed_build_is_reported_as_rollback_on_the_failed_step(onprem_dir):
    files = {"metrics-10.json": {"metrics": []}, "failure-analysis.json": {"verdict": "needs_human", "findings": []}}

    snapshot = _execute(_run("bad_canary", onprem_dir, jenkins=FakeJenkins("FAILED", files=files)))

    assert snapshot["outcome"] == "rolled_back"
    assert _states(snapshot)["canary-10"] == "fail"


def test_missing_baseline_is_deployed_before_the_real_release(onprem_dir):
    jenkins = FakeJenkins()

    _execute(_run("bad_canary", onprem_dir, jenkins=jenkins, ready=False))

    assert jenkins.triggered == ["good", "bad-canary"]


def test_jenkins_input_during_the_baseline_build_reaches_the_console(onprem_dir):
    jenkins = FakeJenkins(inputs=1)

    snapshot = _execute(_run("normal", onprem_dir, jenkins=jenkins, ready=False), ["approve"])

    assert jenkins.answers == [True]
    assert snapshot["outcome"] == "deployed"


def test_jenkins_input_is_answered_from_the_console(onprem_dir):
    jenkins = FakeJenkins(inputs=1)

    snapshot = _execute(_run("normal", onprem_dir, jenkins=jenkins), ["approve"])

    assert jenkins.answers == [True]
    assert snapshot["outcome"] == "deployed"


def test_previous_builds_files_are_ignored_before_the_rollout_stage(tmp_path):
    stale = {"gate-10.json": {"verdict": "pass"}, "metrics-10.json": {"metrics": []}}
    options = Options(SCENARIO["normal"])
    board = Board(tmp_path / "board.json", options, "r")
    deps = Deps(run_id="r", options=options, board=board, delta=FakeDelta(), dast=FakeDast(), jenkins=FakeJenkins(files=stale),
                onprem_dir=tmp_path, service_ready=lambda: True, on_release_start=lambda: None, sleep=lambda _: None)

    sync_stages(deps, 1, {"stages": [{"name": "테스트", "status": "IN_PROGRESS"}]})

    assert _states(board.snapshot())["canary-10"] == "pending"


@pytest.mark.parametrize("gates", [{}, {"onprem": "pass"}, {"onprem": "pass", "dast": "needs_human"}])
def test_deploy_refuses_without_a_pass_record_for_every_gate(gates):
    with pytest.raises(Stopped):
        assert_gates_passed(gates)


def test_deploy_accepts_pass_and_human_approval():
    assert_gates_passed({"onprem": "approved", "dast": "pass"})


@pytest.mark.parametrize("verdicts,expected", [
    (["pass", "pass"], "pass"), (["pass", "needs_human"], "needs_human"), (["pass", None], "needs_human"), ([], "needs_human"),
])
def test_combined_verdict_only_passes_when_everything_passes(verdicts, expected):
    assert combined_verdict(verdicts) == expected


def test_dast_request_keeps_only_the_observation_fields():
    run = {"run_id": "r", "results": [{"finding_id": "F", "status": "REPRODUCED", "evidence": {"cost_usd": 1}}]}

    body = dast_request(run, assessment_id="a", target="t")

    assert body["evidence"]["dast"]["results"] == [{"finding_id": "F", "title": "", "status": "REPRODUCED", "message": ""}]


class AlwaysReproducedDast(FakeDast):
    def run(self, run_id):
        return {"status": "done", "results": [{"finding_id": "F", "title": "XSS", "status": "REPRODUCED", "message": "m"}]}


def test_fixed_build_that_still_reproduces_waits_for_a_person_and_can_stop(onprem_dir):
    dast, jenkins = AlwaysReproducedDast(), FakeJenkins()

    snapshot = _execute(_run("vulnerable", onprem_dir, dast=dast, jenkins=jenkins), ["reverify", "stop"])

    assert dast.scenarios == ["vulnerable", "fixed"]
    assert snapshot["outcome"] == "stopped"
    assert jenkins.triggered == []
    assert _states(snapshot)["dast-judge"] == "fail"


@pytest.mark.parametrize("state,expected", [
    ({"halt": "x", "dast_before": {}}, "halted"),
    ({"dast_before": {}}, "ask_reverify"),
    ({"dast_before": None, "pending_gate": {"key": "dast"}}, "ask_dast"),
    ({"dast_before": None, "pending_gate": None}, "deploy_check"),
])
def test_dast_judgment_routes(state, expected):
    assert route_dast_judge(state) == expected


def test_release_start_refuses_without_gate_records_and_never_triggers(tmp_path):
    options, jenkins = Options(SCENARIO["normal"]), FakeJenkins()
    deps = Deps(run_id="r", options=options, board=Board(tmp_path / "board.json", options, "r"), delta=FakeDelta(), dast=FakeDast(),
                jenkins=jenkins, onprem_dir=tmp_path, service_ready=lambda: True, on_release_start=lambda: None, sleep=lambda _: None)

    with pytest.raises(Stopped):
        Steps(deps).release_start({"gates": {"onprem": "pass"}})

    assert jenkins.triggered == []


class FlakyJenkins(FakeJenkins):
    """호출마다 정해진 횟수만큼 조회가 실패한다."""

    def __init__(self, failures, **kwargs):
        super().__init__(**kwargs)
        self.failures = {"find_build": failures, "describe": failures}

    def _flaky(self, name):
        if self.failures[name] > 0:
            self.failures[name] -= 1
            raise ServiceUnavailable("시간 초과")

    def find_build(self, run_key):
        self._flaky("find_build")
        return super().find_build(run_key)

    def describe(self, build):
        self._flaky("describe")
        return super().describe(build)


def test_short_jenkins_read_failures_do_not_end_the_run(onprem_dir):
    jenkins = FlakyJenkins(failures=READ_ATTEMPTS - 1)

    snapshot = _execute(_run("normal", onprem_dir, jenkins=jenkins))

    assert snapshot["outcome"] == "deployed"
    assert jenkins.triggered == ["good"]


def test_jenkins_that_stays_unreachable_stops_and_says_so(onprem_dir):
    jenkins = FlakyJenkins(failures=10_000)

    snapshot = _execute(_run("normal", onprem_dir, jenkins=jenkins))

    assert snapshot["outcome"] == "stopped"
    assert jenkins.triggered == []
