"""실제 프로세스를 강제 종료해 재개를 확인한다. 같은 빌드·배포가 두 번 시작되면 안 된다."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HELPER = Path(__file__).with_name("killable.py")
KILLED = 9


def _launch(base, kill_at, dast="NOT_REPRODUCED", answer="approve"):
    return subprocess.run([sys.executable, str(HELPER), str(base), kill_at, dast, answer],
                          capture_output=True, text=True, timeout=60, check=False)


def _calls(base, name):
    rows = [json.loads(line) for line in (base / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    return [row["value"] for row in rows if row["call"] == name]


def _final(done):
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.splitlines()[-1])


@pytest.mark.parametrize("kill_at", ["after_trigger_build", "wait_build", "after_trigger_deploy", "wait_deploy"])
def test_killed_job_resumes_without_a_second_trigger(tmp_path, kill_at):
    assert _launch(tmp_path, kill_at).returncode == KILLED

    snapshot = _final(_launch(tmp_path, "never"))

    assert snapshot["outcome"] == "verified"
    jobs = [job for job, _ in _calls(tmp_path, "trigger")]
    assert jobs.count("build") == 1 and jobs.count("deploy") == 1


def test_killed_while_waiting_for_a_person_asks_again_and_never_repeats_dast(tmp_path):
    assert _launch(tmp_path, "waiting", "REPRODUCED").returncode == KILLED
    assert len(_calls(tmp_path, "dast")) == 1

    done = _launch(tmp_path, "never", "REPRODUCED", "approve")

    assert json.loads(done.stdout.splitlines()[0]) == {"seen_decision": "dast"}
    assert _final(done)["outcome"] == "verified"
    assert len(_calls(tmp_path, "dast")) == 1


def test_stop_after_restart_never_verifies(tmp_path):
    assert _launch(tmp_path, "waiting", "REPRODUCED").returncode == KILLED

    snapshot = _final(_launch(tmp_path, "never", "REPRODUCED", "stop"))

    assert snapshot["outcome"] == "stopped"
