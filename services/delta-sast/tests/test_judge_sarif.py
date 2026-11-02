import asyncio
import json
import shutil

import pytest

from delta_sast.classify import RuleCache, cache_key, classify
from delta_sast.judge import FakeClient, mask_secrets
from delta_sast.policy import load_policy
from delta_sast.sarif import Columns
from delta_sast.scan import run_fast

needs_opengrep = pytest.mark.skipif(shutil.which("opengrep") is None, reason="opengrep 없음")
CLEAN = "shop.filters.clean"


def vulnerable(path="shop/filters.py", line=6, payload="img_onerror"):
    return {"verdict": "vulnerable", "reason": "script 태그만 지워 다른 태그가 남는다.",
            "cited": [{"path": path, "line": line}], "payload_template": payload}


def run(root, client, cache, payload_mode="ai"):
    return asyncio.run(run_fast(root, client=client, cache=cache, payload_mode=payload_mode))


def pending_clean(result):
    return next((f, j) for f, j in result.findings if f.sans and f.sans[0].name == CLEAN)


@needs_opengrep
def test_llm_judges_only_pending_and_keeps_allowed_payload(fixtures):
    client = FakeClient({CLEAN: vulnerable()})
    result = run(fixtures / "misc", client, RuleCache())
    assert client.calls == 1  # 규칙으로 판정된 경로는 LLM에 가지 않는다
    flow, judgment = pending_clean(result)
    assert (judgment.by, judgment.verdict, judgment.payload) == ("llm", "vulnerable", "img_onerror")
    assert judgment.cache_key == cache_key(flow)
    assert [f"{c.path}:{c.line}" for c in judgment.cited] == ["shop/filters.py:6"]


@needs_opengrep
def test_citation_outside_path_is_rejected(fixtures):
    result = run(fixtures / "misc", FakeClient({CLEAN: vulnerable(line=999)}), RuleCache())
    _, judgment = pending_clean(result)
    assert (judgment.by, judgment.verdict) == ("llm", "uncertain")
    assert "999" in judgment.reason


@needs_opengrep
def test_payload_outside_context_or_preset_is_dropped(fixtures):
    result = run(fixtures / "misc", FakeClient({CLEAN: vulnerable(payload="sql_union")}), RuleCache())
    assert pending_clean(result)[1].payload is None
    result = run(fixtures / "misc", FakeClient({CLEAN: vulnerable()}), RuleCache(), payload_mode="preset")
    assert pending_clean(result)[1].payload is None


@needs_opengrep
def test_llm_failure_becomes_uncertain(fixtures):
    result = run(fixtures / "misc", FakeClient({CLEAN: TimeoutError("느림")}), RuleCache())
    _, judgment = pending_clean(result)
    assert judgment.verdict == "uncertain" and "TimeoutError" in judgment.reason
    assert result.llm["llm_failed"] == 1


@needs_opengrep
def test_second_run_makes_no_llm_call_and_same_sarif(fixtures, tmp_path):
    cache_path = tmp_path / "cache.json"
    first_client = FakeClient({CLEAN: vulnerable()})
    cache = RuleCache(cache_path)
    first = run(fixtures / "misc", first_client, cache)
    cache.save()
    second_client = FakeClient({CLEAN: vulnerable()})
    second = run(fixtures / "misc", second_client, RuleCache(cache_path))
    assert (first_client.calls, second_client.calls) == (1, 0)
    a = first.sarif(run_id="r1")["runs"][0]["results"]
    b = second.sarif(run_id="r1")["runs"][0]["results"]
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


@needs_opengrep
def test_learn_turns_agreed_judgment_into_rule(fixtures):
    policy = load_policy()
    cache = RuleCache()
    result = run(fixtures / "misc", FakeClient({CLEAN: vulnerable()}), cache)
    flow, _ = pending_clean(result)
    assert cache.learn(flow, "vulnerable", "NOT_REPRODUCED", "r1") == "conflict"
    assert classify(flow, policy, cache).by == "pending"
    assert cache.learn(flow, "vulnerable", "REPRODUCED", "r1") == "ineffective"
    assert (classify(flow, policy, cache).by, classify(flow, policy, cache).verdict) == ("cache", "vulnerable")


@needs_opengrep
def test_sarif_shape_for_dast(fixtures):
    result = run(fixtures / "board", None, RuleCache())
    sarif = result.sarif(run_id="run-42", commit="abc1234")
    assert sarif["version"] == "2.1.0"
    run0 = sarif["runs"][0]
    assert run0["columnKind"] == "unicodeCodePoints"
    stored = [r for r in run0["results"] if r["ruleId"] == "delta.xss.stored" and r["kind"] == "fail"]
    fields = sorted(r["properties"]["dast"]["field"] for r in stored)
    assert fields == ["body", "comment"]
    body = next(r for r in stored if r["properties"]["dast"]["field"] == "body")
    dast = body["properties"]["dast"]
    assert dast["entry"] == {"method": "POST", "path": "/posts"}
    assert dast["render"] == {"method": "GET", "path": "/posts/<post_id>"}
    assert (dast["context"], dast["stored"], dast["verifiable"]) == ("html_text", True, True)
    steps = body["codeFlows"][0]["threadFlows"][0]["locations"]
    assert steps[0]["kinds"] == ["source"] and steps[-1]["kinds"] == ["sink"]
    assert {s["location"]["physicalLocation"]["artifactLocation"]["uri"] for s in steps} >= {"app/board.py", "app/store.py"}
    assert all(r["kind"] == "pass" for r in run0["results"] if r["properties"]["dast"].get("field") == "title")
    assert run0["properties"]["dast_targets"] == sum(1 for r in run0["results"] if r["kind"] in ("fail", "review"))


@needs_opengrep
def test_fingerprint_ignores_line_moves(fixtures, tmp_path):
    moved = tmp_path / "moved"
    shutil.copytree(fixtures / "board", moved)
    store = moved / "app" / "store.py"
    store.write_text("# 줄을 밀어 본다\n\n\n" + store.read_text(encoding="utf-8"), encoding="utf-8")
    prints = []
    for root in (fixtures / "board", moved):
        sarif = run(root, None, RuleCache()).sarif(run_id="r")
        prints.append(sorted(r["partialFingerprints"]["deltaFlow/v1"] for r in sarif["runs"][0]["results"]))
    assert prints[0] == prints[1]


@needs_opengrep
def test_secret_result_is_not_dast_verifiable(fixtures):
    sarif = run(fixtures / "misc", None, RuleCache()).sarif(run_id="r")
    secrets = [r for r in sarif["runs"][0]["results"] if r["ruleId"] == "delta.secret"]
    assert len(secrets) == 1 and secrets[0]["properties"]["dast"] == {"verifiable": False}
    assert "dev-secret-value-1234" not in json.dumps(sarif, ensure_ascii=False)


def test_mask_secrets():
    text = 'API_KEY = "abcd1234efgh"\nx = "AKIA' + "A" * 16 + '"\npassword: "hunter22"'
    masked = mask_secrets(text)
    assert "abcd1234efgh" not in masked and "AKIA" + "A" * 16 not in masked and "hunter22" not in masked


def test_columns_convert_utf8_bytes_to_code_points():
    class M:
        path, source = "a.py", 'x = "한글"; y = 1\n'

    class P:
        modules = {"a": M}

    cols = Columns(P)
    byte_col = len('x = "한글"; '.encode("utf-8")) + 1
    assert cols.char("a.py", 1, byte_col) == len('x = "한글"; ') + 1
