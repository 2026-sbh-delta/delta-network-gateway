"""실제 프로세스를 강제 종료해 재개를 확인한다. 같은 배포가 두 번 시작되면 안 된다."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HELPER = Path(__file__).with_name("killable.py")
KILLED = 9


@pytest.fixture
def base(tmp_path):
    onprem = tmp_path / "onprem"
    onprem.mkdir()
    for kind in ("runtime_security", "migration_parity"):
        (onprem / f"{kind}.request.json").write_text(json.dumps({"kind": kind, "evidence": {}}), encoding="utf-8")
    return tmp_path


def _launch(base, kill_at, scenario="normal", answer="reverify"):
    return subprocess.run([sys.executable, str(HELPER), str(base), kill_at, scenario, answer],
                          capture_output=True, text=True, timeout=60, check=False)


def _calls(base, name):
    rows = [json.loads(line) for line in (base / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    return [row["value"] for row in rows if row["call"] == name]


def _final(done):
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.splitlines()[-1])


@pytest.mark.parametrize("kill_at", ["after_trigger", "deploy_wait"])
def test_killed_deploy_resumes_without_a_second_trigger(base, kill_at):
    assert _launch(base, kill_at).returncode == KILLED

    snapshot = _final(_launch(base, "never"))

    assert snapshot["outcome"] == "deployed"
    assert len(_calls(base, "trigger")) == 1


def test_killed_while_waiting_for_a_person_asks_again_and_never_repeats_dast(base):
    assert _launch(base, "waiting", "vulnerable").returncode == KILLED
    assert _calls(base, "dast") == ["vulnerable"]

    done = _launch(base, "never", "vulnerable", "reverify")

    assert json.loads(done.stdout.splitlines()[0]) == {"seen_decision": "dast-judge"}
    assert _final(done)["outcome"] == "deployed"
    assert _calls(base, "dast") == ["vulnerable", "fixed"]
    assert len(_calls(base, "trigger")) == 1


def test_stop_after_restart_never_deploys(base):
    assert _launch(base, "waiting", "vulnerable").returncode == KILLED

    snapshot = _final(_launch(base, "never", "vulnerable", "stop"))

    assert snapshot["outcome"] == "stopped"
    assert not (base / "calls.jsonl").read_text(encoding="utf-8").count('"trigger"')


def test_killed_during_the_shift_resumes_at_the_same_step(base):
    assert _launch(base, "weight_50", "migration", "approve").returncode == KILLED
    assert _calls(base, "weight") == [10, 50]

    snapshot = _final(_launch(base, "never", "migration", "approve"))

    assert snapshot["outcome"] == "migrated"
    assert _calls(base, "weight") == [10, 50, 50, 100]
    assert len(_calls(base, "cut_over")) == 1


def test_killed_while_moving_writes_finishes_the_cutover_and_never_returns_the_traffic(base):
    assert _launch(base, "cut_over", "migration", "approve").returncode == KILLED
    assert 0 not in _calls(base, "weight")

    snapshot = _final(_launch(base, "never", "migration", "approve"))

    assert snapshot["outcome"] == "migrated"
    assert 0 not in _calls(base, "weight")
    assert len(_calls(base, "cut_over")) == 2
