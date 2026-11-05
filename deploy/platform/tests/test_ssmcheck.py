"""연결·배포 검증이 실패를 성공으로 오판하지 않는지. AWS는 가짜 응답으로 대신한다."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ssmcheck  # noqa: E402

OUTPUTS = {
    "region": {"value": "ap-northeast-2"},
    "instance_ids": {"value": {"a": "i-a", "b": "i-b"}},
    "internal_names": {"value": {"a": "a.cp.internal", "b": "b.cp.internal"}},
    "service_ports": {"value": {"a": [8080], "b": [8790, 8091]}},
    "links": {"value": [{"from": "a", "to": "b", "port": 8790}]},
}


def fake_aws(invocations: dict[str, dict]):
    """send-command는 인스턴스별 명령 ID를, get-command-invocation은 미리 정한 결과를 돌려준다."""
    def run(args):
        if args[:2] == ["ssm", "send-command"]:
            instance = args[args.index("--instance-ids") + 1]
            return json.dumps({"Command": {"CommandId": f"cmd-{instance}"}})
        if args[:2] == ["ssm", "get-command-invocation"]:
            instance = args[args.index("--instance-id") + 1]
            inv = invocations[instance]
            if isinstance(inv, Exception):
                raise inv
            return json.dumps(inv)
        raise AssertionError(args)
    return run


def ok_output(lines):
    return "\n".join(lines + ["END"]) + "\n"


GOOD = {
    "i-a": {"Status": "Success", "ResponseCode": 0, "StandardOutputContent": ok_output([
        "RESULT b.cp.internal:8790 open -", "RESULT b.cp.internal:8091 closed timeout"])},
    "i-b": {"Status": "Success", "ResponseCode": 0, "StandardOutputContent": ok_output([
        "RESULT a.cp.internal:8080 closed timeout"])},
}


def run_links(invocations):
    lines = []
    code = ssmcheck.run_links(OUTPUTS, run=fake_aws(invocations), sleep=lambda _s: None, out=lines.append,
                              wait_timeout=0)
    return code, "\n".join(lines)


def test_expected_checks_come_from_outputs():
    plan = ssmcheck.expected_checks(OUTPUTS)
    assert sum(len(v) for v in plan.values()) == 3
    assert [(c.key, c.want) for c in plan["a"]] == [("b.cp.internal:8790", "open"), ("b.cp.internal:8091", "closed")]


def test_all_expected_results_pass():
    code, text = run_links(GOOD)
    assert code == 0 and "기대 3건 · 결과 3건 · 정상 3 · 불일치 0 · 검사 오류 0" in text


@pytest.mark.parametrize("change", [
    {"Status": "Failed", "ResponseCode": 1, "StandardOutputContent": ""},
    {"Status": "Success", "ResponseCode": 2, "StandardOutputContent": GOOD["i-a"]["StandardOutputContent"]},
    {"Status": "Success", "ResponseCode": 0, "StandardOutputContent": ""},
    {"Status": "Success", "ResponseCode": 0, "StandardOutputContent": "None"},
    {"Status": "Success", "ResponseCode": 0, "StandardOutputContent": ok_output(["RESULT b.cp.internal:8790 open -"])},
    {"Status": "Success", "ResponseCode": 0, "StandardOutputContent": ok_output([
        "RESULT b.cp.internal:8790 closed timeout", "RESULT b.cp.internal:8091 closed timeout"])},
    {"Status": "Success", "ResponseCode": 0, "StandardOutputContent": ok_output([
        "RESULT b.cp.internal:8790 open -", "RESULT b.cp.internal:8790 open -", "RESULT b.cp.internal:8091 closed timeout"])},
    {"Status": "Success", "ResponseCode": 0, "StandardOutputContent": ok_output([
        "RESULT b.cp.internal:8790 open -", "RESULT b.cp.internal:8091 closed timeout", "RESULT x.cp.internal:1 open -"])},
    {"Status": "Success", "ResponseCode": 0, "StandardOutputContent": ok_output([
        "RESULT b.cp.internal:8790 error dns", "RESULT b.cp.internal:8091 closed timeout"])},
    {"Status": "Cancelled", "ResponseCode": -1},
    {"Status": "TimedOut", "ResponseCode": -1},
], ids=["ssm-failed", "success-but-nonzero-exit", "empty-output", "none-output", "missing-result", "mismatch",
        "duplicate", "unexpected-item", "dns-error-is-not-closed", "cancelled", "timed-out"])
def test_every_failure_mode_fails(change):
    code, text = run_links({**GOOD, "i-a": change})
    assert code == 1, text
    assert "모든 연결이 설계와 같다" not in text


def test_lookup_failure_and_wait_limit_fail():
    assert run_links({**GOOD, "i-a": ssmcheck.CheckError("AccessDenied")})[0] == 1
    assert run_links({**GOOD, "i-a": {"Status": "InProgress"}})[0] == 1


def test_wait_accepts_only_success_with_zero_exit():
    run = fake_aws({"i-a": {"Status": "Success", "ResponseCode": 0, "StandardOutputContent": "ok"}})
    assert ssmcheck.wait_command("r", "cmd-i-a", "i-a", run=run, sleep=lambda _s: None)["stdout"] == "ok"
    for bad in ({"Status": "Failed", "ResponseCode": 1}, {"Status": "Success", "ResponseCode": 3},
                {"Status": "Weird"}):
        with pytest.raises(ssmcheck.CheckError):
            ssmcheck.wait_command("r", "c", "i-a", run=fake_aws({"i-a": bad}), sleep=lambda _s: None, timeout=0)
    with pytest.raises(ssmcheck.CheckError, match="대기 한도"):
        ssmcheck.wait_command("r", "c", "i-a", run=fake_aws({"i-a": {"Status": "Pending"}}),
                              sleep=lambda _s: None, timeout=0)
