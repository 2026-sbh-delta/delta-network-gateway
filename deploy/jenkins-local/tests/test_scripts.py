from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline" / "scripts"))

import gate  # noqa: E402
import haproxy_ctl  # noqa: E402
import traffic  # noqa: E402
from delta_ai.contract import AssessmentRequest  # noqa: E402


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x", code, "error", {}, io.BytesIO())


def test_deployment_request_matches_service_contract():
    body = gate.deployment_request(assessment_id="a", service_id="demo", commit="c", stage="traffic-10",
                                   status="succeeded", target="aws", traffic_percent=10, exit_code=0, log_tail="log",
                                   metrics=({"name": "error_rate", "value": 0.1, "threshold": 0.05, "samples": 40},))

    assert AssessmentRequest.model_validate(body).evidence.deployment.traffic_percent == 10


def test_security_request_matches_service_contract():
    body = gate.security_request(assessment_id="a", service_id="demo", commit="c", sarif={"runs": []},
                                 code_files={"app/a.py": "x"})

    assert AssessmentRequest.model_validate(body).kind == "code_security"


@pytest.mark.parametrize("verdict,code", [("pass", gate.EXIT_PASS), ("fix", gate.EXIT_FIX),
                                          ("needs_human", gate.EXIT_NEEDS_HUMAN), ("weird", gate.EXIT_UNAVAILABLE)])
def test_verdict_maps_to_exit_code(verdict, code):
    assert gate.exit_code_for({"verdict": verdict}) == code


def test_call_gate_retries_service_unavailable_once():
    calls = []

    def poster(*args):
        calls.append(args)
        if len(calls) == 1:
            raise _http_error(503)
        return {"verdict": "pass"}

    assert gate.call_gate("http://x", "t", {}, poster=poster, sleep=lambda _: None) == {"verdict": "pass"}
    assert len(calls) == 2


def test_call_gate_does_not_retry_client_errors():
    calls = []

    def poster(*args):
        calls.append(args)
        raise _http_error(401)

    with pytest.raises(gate.GateUnavailable, match="401"):
        gate.call_gate("http://x", "t", {}, poster=poster, sleep=lambda _: None)
    assert len(calls) == 1


def test_call_gate_gives_up_when_unreachable():
    def poster(*_):
        raise urllib.error.URLError("refused")

    with pytest.raises(gate.GateUnavailable):
        gate.call_gate("http://x", "t", {}, poster=poster, sleep=lambda _: None)


def test_main_returns_unavailable_when_service_is_down(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DELTA_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("DELTA_API_TOKEN", "t")
    monkeypatch.setattr(gate.time, "sleep", lambda _: None)
    events = tmp_path / "events.jsonl"

    code = gate.main(["deployment_gate", "--id", "a", "--stage", "traffic-10", "--out", str(tmp_path / "r.json"),
                      "--events", str(events)])

    assert code == gate.EXIT_UNAVAILABLE
    assert json.loads(events.read_text())["state"] == "unavailable"
    assert "t" not in capsys.readouterr().out


def test_read_tail_keeps_the_end_and_tolerates_missing_file(tmp_path):
    log = tmp_path / "x.log"
    log.write_text("a" * 10 + "END")

    assert gate.read_tail(log, limit=3) == "END"
    assert gate.read_tail(tmp_path / "none.log") == ""


def test_collect_code_uses_relative_paths_and_skips_other_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "a.py").write_text("x = 1\n")
    (tmp_path / "app" / "notes.txt").write_text("skip")

    assert gate.collect_code(Path("app")) == {"app/a.py": "x = 1\n"}


def _samples(version: str | None, status: int, count: int, seconds: float = 0.01) -> list[traffic.Sample]:
    return [traffic.Sample(version, status, seconds)] * count


def test_summary_measures_only_the_new_version():
    samples = [*_samples("2", 500, 10), *_samples("2", 200, 30), *_samples("1", 500, 60)]

    summary = traffic.summarize(samples, "2", max_error_rate=0.05, max_p95_ms=500)

    assert summary["canary_requests"] == 40
    assert summary["metrics"][0]["value"] == 0.25
    assert traffic.exceeded(summary)


def test_summary_blames_new_version_for_unattributed_failures():
    samples = [*_samples("2", 200, 30), *_samples(None, 503, 10)]

    summary = traffic.summarize(samples, "2", max_error_rate=0.05, max_p95_ms=500)

    assert summary["metrics"][0]["value"] == 0.25
    assert summary["unattributed_failures"] == 10


def test_small_sample_does_not_trigger_automatic_rollback():
    summary = traffic.summarize(_samples("2", 500, 5), "2", max_error_rate=0.05, max_p95_ms=500)

    assert not traffic.exceeded(summary)


def test_summary_without_new_version_samples_has_unknown_values():
    summary = traffic.summarize(_samples("1", 200, 10), "2", max_error_rate=0.05, max_p95_ms=500)

    assert summary["metrics"][0]["value"] is None
    assert summary["metrics"][1]["value"] is None


def test_healthy_summary_is_not_exceeded():
    assert not traffic.exceeded(traffic.summarize(_samples("2", 200, 100), "2", max_error_rate=0.05, max_p95_ms=500))


def test_slow_new_version_is_exceeded():
    assert traffic.exceeded(traffic.summarize(_samples("2", 200, 100, seconds=2), "2", max_error_rate=0.05,
                                              max_p95_ms=500))


def test_split_raises_canary_before_lowering_stable():
    assert haproxy_ctl.split_commands("blue", "green", 10) == ("set weight demo/green 10", "set weight demo/blue 90")


@pytest.mark.parametrize("percent", [-1, 101])
def test_split_rejects_invalid_percent(percent):
    with pytest.raises(ValueError):
        haproxy_ctl.split_commands("blue", "green", percent)


def test_parse_weight_reads_current_value():
    assert haproxy_ctl.parse_weight("10 (initial 100)\n") == 10


def test_active_slot_is_the_heavier_one():
    assert haproxy_ctl.active_slot({"blue": 0, "green": 100}) == "green"
    assert haproxy_ctl.other_slot("green") == "blue"


def test_server_status_reads_stat_csv():
    csv = "# pxname,svname,status\ndemo,blue,UP\ndemo,green,DOWN\n"

    assert haproxy_ctl.server_status(csv, "blue") == "UP"
    assert haproxy_ctl.server_status(csv, "green") == "DOWN"
    assert haproxy_ctl.server_status("", "blue") is None
