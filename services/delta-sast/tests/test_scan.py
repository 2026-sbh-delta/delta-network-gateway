import shutil

import pytest

from delta_sast.classify import RuleCache, cache_key, classify
from delta_sast.graph import html_context
from delta_sast.policy import load_policy
from delta_sast.scan import scan

needs_opengrep = pytest.mark.skipif(shutil.which("opengrep") is None, reason="opengrep 없음")


def summary(result):
    """(취약 유형, 맥락, 입력 칸, 판정 주체, 판정) 목록."""
    return {(f.vuln, f.context, f.source.field_name, j.by, j.verdict) for f, j in result.findings}


def find(result, field_name, context):
    matches = [(f, j) for f, j in result.findings if f.source.field_name == field_name and f.context == context]
    assert matches, f"{field_name}/{context} 경로가 없다: {summary(result)}"
    return matches


@pytest.mark.parametrize("prefix, expected", [
    ("<p>", "html_text"),
    ('<a href="', "url"),
    ('<a href="/posts/', "url"),
    ('<div title="', "html_attr"),
    ("<div ", "html_attr"),
    ("<script>var x = '", "js_string"),
    ("<script>a</script><p>", "html_text"),
    ('<img onerror="', "js_string"),
])
def test_html_context(prefix, expected):
    assert html_context(prefix) == expected


@needs_opengrep
def test_board_stored_xss_crosses_files_and_store(fixtures):
    result = scan(fixtures / "board")
    body = find(result, "body", "html_text")
    flow, judgment = body[0]
    assert (judgment.by, judgment.verdict) == ("rule", "candidate")
    assert flow.stores == ("app.store.posts",)
    assert [r.text() for r in flow.entry] == ["POST /posts"]
    assert [r.text() for r in flow.render] == ["GET /posts/<post_id>"]
    paths = {s.path for s in flow.steps}
    assert {"app/board.py", "app/store.py"} <= paths

    comment, judgment = find(result, "comment", "html_text")[0]
    assert (judgment.by, judgment.verdict) == ("rule", "candidate")
    assert comment.stores == ("app.store.posts",)
    assert [r.text() for r in comment.entry] == ["POST /posts/<post_id>/comments"]
    assert [r.text() for r in comment.render] == ["GET /posts/<post_id>"]
    assert {"app/comments.py", "app/store.py", "app/board.py"} <= {s.path for s in comment.steps}


@needs_opengrep
def test_board_escaped_title_is_safe_by_rule(fixtures):
    result = scan(fixtures / "board")
    titles = find(result, "title", "html_text")
    assert all(j.verdict == "safe" and j.by == "rule" for _, j in titles)
    assert all(any(s.name == "html.escape" for s in f.sans) for f, _ in titles)


@needs_opengrep
def test_misc_flows(fixtures):
    result = scan(fixtures / "misc")
    got = summary(result)
    assert ("sqli", "sql", "q", "rule", "candidate") in got
    assert ("xss", "html_text", "q", "rule", "safe") in got  # markupsafe.escape
    assert ("xss", "html_text", "q", "pending", "uncertain") in got  # 직접 만든 clean()
    assert ("cmdi", "shell", "host", "rule", "candidate") in got
    assert ("xss", "url", "next", "rule", "candidate") in got
    # subprocess 목록 인자, 매개변수 바인딩 쿼리, int() 변환은 경로가 아니다
    assert len([g for g in got if g[0] == "cmdi"]) == 1
    assert len([g for g in got if g[0] == "sqli"]) == 1
    assert not any(g[2] == "count" for g in got)

    body, judgment = find(result, "body", "html_text")[0]
    assert (judgment.by, judgment.verdict) == ("rule", "candidate")
    assert body.stores == ("db.notes",)
    assert [r.text() for r in body.entry] == ["POST /notes"]
    assert [r.text() for r in body.render] == ["GET /notes"]
    title = find(result, "title", "html_text")
    assert all(j.verdict == "safe" for _, j in title)


@needs_opengrep
def test_misc_secret_is_found_and_redacted(fixtures):
    result = scan(fixtures / "misc")
    assert [s.loc.path for s in result.secrets] == ["shop/app.py"]
    assert "dev-secret-value-1234" not in result.secrets[0].preview


@needs_opengrep
def test_custom_filter_uses_cache_on_second_run(fixtures, tmp_path):
    policy = load_policy()
    cache = RuleCache(tmp_path / "cache.json")
    result = scan(fixtures / "misc", policy, cache)
    flow, judgment = next((f, j) for f, j in result.findings if j.by == "pending")
    assert judgment.cache_key == cache_key(flow)
    assert flow.sans[0].internal and flow.sans[0].code_hash.startswith("sha256:")

    cache.put(judgment.cache_key, {"sanitizer": flow.sans[0].name, "context": flow.context,
                                   "verdict": "ineffective", "evidence": {"dast": "test"}})
    cache.save()
    again = classify(flow, policy, RuleCache(tmp_path / "cache.json"))
    assert (again.by, again.verdict) == ("cache", "vulnerable")


@needs_opengrep
def test_same_input_same_result(fixtures):
    a, b = scan(fixtures / "misc"), scan(fixtures / "misc")
    assert [(f.ident(), j.verdict) for f, j in a.findings] == [(f.ident(), j.verdict) for f, j in b.findings]
