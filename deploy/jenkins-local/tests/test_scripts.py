from __future__ import annotations

import io
import json
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline" / "scripts"))

import build_image  # noqa: E402
import gate  # noqa: E402
import gate_ready  # noqa: E402
import haproxy_ctl  # noqa: E402
import sast_findings  # noqa: E402
import traffic  # noqa: E402
from delta_ai.contract import AssessmentRequest  # noqa: E402


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x", code, "error", {}, io.BytesIO())


def test_deployment_request_matches_service_contract():
    body = gate.deployment_request(assessment_id="a", service_id="demo", commit="c", stage="traffic-10",
                                   status="succeeded", target="aws", traffic_percent=10, exit_code=0, log_tail="log",
                                   metrics=({"name": "error_rate", "value": 0.1, "threshold": 0.05, "samples": 40},))

    assert AssessmentRequest.model_validate(body).evidence.deployment.traffic_percent == 10


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


@pytest.mark.parametrize("results,code", [([], sast_findings.EXIT_CLEAN), ([{"ruleId": "x"}], sast_findings.EXIT_NEEDS_HUMAN)])
def test_sast_findings_requires_human_only_when_something_was_found(tmp_path, results, code):
    path = tmp_path / "sast.sarif"
    path.write_text(json.dumps({"runs": [{"results": results}]}), encoding="utf-8")

    assert sast_findings.main([str(path)]) == code


@pytest.mark.parametrize("content", ["not json", "{}", '{"runs": [{}]}'])
def test_unreadable_sarif_does_not_pass(tmp_path, content):
    path = tmp_path / "sast.sarif"
    path.write_text(content, encoding="utf-8")

    with pytest.raises((ValueError, KeyError)):
        sast_findings.main([str(path)])


def test_ready_uses_a_token_protected_endpoint():
    calls = []

    def getter(url, token, timeout):
        calls.append((url, token))
        return {"candidates": []}

    gate_ready.check_ready("http://x", "t", getter=getter, sleep=lambda _: None)

    assert calls == [("http://x/v1/rule-candidates", "t")]


def test_ready_rejects_a_bad_token_without_retrying():
    calls = []

    def getter(*args):
        calls.append(args)
        raise _http_error(401)

    with pytest.raises(gate.GateUnavailable, match="HTTP 401"):
        gate_ready.check_ready("http://x", "t", getter=getter, sleep=lambda _: None)
    assert len(calls) == 1


def test_ready_retries_a_failed_connection_then_gives_up():
    calls = []

    def getter(*args):
        calls.append(args)
        raise urllib.error.URLError("refused")

    with pytest.raises(gate.GateUnavailable, match="URLError"):
        gate_ready.check_ready("http://x", "t", getter=getter, sleep=lambda _: None)
    assert len(calls) == gate.MAX_ATTEMPTS


def test_ready_main_returns_unavailable_exit_code(monkeypatch, capsys):
    monkeypatch.setenv("DELTA_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("DELTA_API_TOKEN", "secret-token")
    monkeypatch.setattr(gate_ready.time, "sleep", lambda _: None)

    assert gate_ready.main() == gate.EXIT_UNAVAILABLE
    assert "secret-token" not in capsys.readouterr().err


class _Docker:
    """docker 호출을 기록하고 정해 둔 결과를 돌려준다."""

    def __init__(self, *, built=0, inspected="sha256:abc linux/amd64"):
        self.built, self.inspected, self.calls = built, inspected, []

    def __call__(self, command):
        self.calls.append(list(command))
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, self.inspected, "")
        return subprocess.CompletedProcess(command, self.built, "", "실패")


def test_build_records_the_image_and_platform(tmp_path):
    docker, out = _Docker(), tmp_path / "image.json"

    assert build_image.build("t:1", "app", "linux/amd64", out, docker) == 0

    assert json.loads(out.read_text()) == {"tag": "t:1", "image_id": "sha256:abc", "platform": "linux/amd64"}


def test_buildx_build_turns_off_attestations_so_the_digest_stays_single():
    command = build_image.build_command("t:1", "app", "linux/amd64")

    assert {"--provenance=false", "--sbom=false", "--load"} <= set(command)


def test_image_of_another_architecture_fails_and_is_not_recorded(tmp_path):
    out = tmp_path / "image.json"

    code = build_image.build("t:1", "app", "linux/amd64", out, _Docker(inspected="sha256:abc linux/arm64"))

    assert code == build_image.EXIT_WRONG_PLATFORM
    assert not out.exists()


def test_failed_build_is_not_recorded(tmp_path):
    out = tmp_path / "image.json"

    assert build_image.build("t:1", "app", "linux/amd64", out, _Docker(built=1)) == build_image.EXIT_BUILD_FAILED
    assert not out.exists()
