from types import SimpleNamespace

import pytest

from delta_pipeline import run as run_module
from test_flow import _run

REQUEST = {"node_id": "dast", "prompt": "Review scope", "options": [("approve", "Proceed"), ("stop", "Stop")]}


def clocked_wait(monkeypatch, run, *, answer_at=None, cancel_at=None):
    clock = SimpleNamespace(seconds=0)
    monkeypatch.setattr(run_module, "time", SimpleNamespace(time=lambda: clock.seconds))

    def wait(timeout):
        clock.seconds += 60
        if cancel_at is not None and clock.seconds >= cancel_at:
            run.cancel()
        if answer_at is not None and clock.seconds >= answer_at:
            assert run.answer("approve")
            return True
        return False

    monkeypatch.setattr(run._answered, "wait", wait)
    return clock


def test_confirmation_can_arrive_after_old_thirty_minute_limit(tmp_path, monkeypatch):
    run = _run(tmp_path)
    clock = clocked_wait(monkeypatch, run, answer_at=2 * 60 * 60)
    assert run._wait_for_answer(REQUEST) == "approve"
    assert clock.seconds == 7200
    assert not run._waiting


def test_no_confirmation_stops_after_three_hours(tmp_path, monkeypatch):
    run = _run(tmp_path)
    clock = clocked_wait(monkeypatch, run)
    with pytest.raises(TimeoutError, match="사람 확인 시간이 지났다"):
        run._wait_for_answer(REQUEST)
    assert clock.seconds == 3 * 60 * 60
    assert not run.answer("approve")
    assert not any(n.get("decision") for n in run.snapshot()["nodes"])


def test_onprem_cancel_does_not_wait_for_deadline(tmp_path, monkeypatch):
    from dataclasses import replace
    run = _run(tmp_path)
    run.options = replace(run.options, execution_cloud="onprem")
    clock = clocked_wait(monkeypatch, run, cancel_at=60)
    assert run._wait_for_answer(REQUEST) == "stop"
    assert clock.seconds == 60
