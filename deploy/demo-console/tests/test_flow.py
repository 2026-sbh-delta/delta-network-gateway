from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clients import ServiceUnavailable  # noqa: E402
from flow import SCENARIOS, Options, Run, combined_verdict, dast_request  # noqa: E402

SCENARIO = {s.id: s for s in SCENARIOS}


class FakeDelta:
    def __init__(self, *, onprem="pass", fail=False):
        self.onprem, self.fail, self.kinds = onprem, fail, []

    def assess(self, body):
        if self.fail:
            raise ServiceUnavailable("죽음")
        self.kinds.append(body["kind"])
        if body["kind"] == "dast_verification":
            statuses = [r["status"] for r in body["evidence"]["dast"]["results"]]
            return {"verdict": "pass" if statuses == ["NOT_REPRODUCED"] else "needs_human", "findings": []}
        return {"verdict": self.onprem, "findings": [{"rule_id": "R", "status": "fail", "severity": "high"}]}


class FakeDast:
    def __init__(self):
        self.scenarios = []

    def config(self):
        return {"sample_report": {"findings": []}, "target_ids": ["local-fixture"]}

    def start(self, scenario, planner, report):
        self.scenarios.append(scenario)
        return f"run-{scenario}"

    def run(self, run_id):
        status = "NOT_REPRODUCED" if run_id.endswith("fixed") else "REPRODUCED"
        return {"status": "done", "results": [{"finding_id": "F", "title": "XSS", "status": status, "message": "m"}]}


class FakeJenkins:
    def __init__(self, final="SUCCESS", *, files=None, inputs=0):
        self.final, self.files, self.triggered = final, files or {}, []
        self.inputs, self.answers = inputs, []

    def trigger(self, scenario):
        self.triggered.append(scenario)
        return len(self.triggered)

    def describe(self, build):
        if self.inputs:
            return {"status": "PAUSED_PENDING_INPUT", "stages": []}
        return {"status": self.final, "stages": [{"name": "단계적 전환", "status": self.final}]}

    def pending_inputs(self, build):
        return [{"id": "HumanGate", "message": "확인"}] if self.inputs else []

    def answer_input(self, build, input_id, *, approve):
        self.answers.append(approve)
        self.inputs = 0

    def file(self, build, name):
        return self.files.get(name)

    def console_url(self, build):
        return f"http://jenkins/{build}"


@pytest.fixture
def onprem_dir(tmp_path):
    for kind in ("runtime_security", "migration_parity"):
        (tmp_path / f"{kind}.request.json").write_text(json.dumps({"kind": kind, "evidence": {}}), encoding="utf-8")
    return tmp_path


def _run(scenario, onprem_dir, *, delta=None, dast=None, jenkins=None, ready=True):
    return Run(Options(SCENARIO[scenario]), delta=delta or FakeDelta(), dast=dast or FakeDast(), jenkins=jenkins or FakeJenkins(),
               onprem_dir=onprem_dir, service_ready=lambda: ready, sleep=lambda _: None)


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


def test_previous_builds_files_are_ignored_before_the_rollout_stage(onprem_dir):
    stale = {"gate-10.json": {"verdict": "pass"}, "metrics-10.json": {"metrics": []}}
    run = _run("normal", onprem_dir, jenkins=FakeJenkins(files=stale))

    run._sync_stages(1, {"stages": [{"name": "테스트", "status": "IN_PROGRESS"}]})

    assert _states(run.snapshot())["canary-10"] == "pending"


@pytest.mark.parametrize("verdicts,expected", [
    (["pass", "pass"], "pass"), (["pass", "needs_human"], "needs_human"), (["pass", None], "needs_human"), ([], "needs_human"),
])
def test_combined_verdict_only_passes_when_everything_passes(verdicts, expected):
    assert combined_verdict(verdicts) == expected


def test_dast_request_keeps_only_the_observation_fields():
    run = {"run_id": "r", "results": [{"finding_id": "F", "status": "REPRODUCED", "evidence": {"cost_usd": 1}}]}

    body = dast_request(run, assessment_id="a", target="t")

    assert body["evidence"]["dast"]["results"] == [{"finding_id": "F", "title": "", "status": "REPRODUCED", "message": ""}]
