"""다언어 엔진: 언어별 규칙 묶음 + Opengrep taint 조각 + 언어 중립 연결기."""

import shutil
from pathlib import Path

import pytest

from delta_sast.locate import locate
from delta_sast.poly.engine import collect, params_of, split_args
from delta_sast.poly.link import link, norm_url, read_columns, written
from delta_sast.scan import scan


def _flows(root: Path):
    return sorted(link(collect(root)), key=lambda f: (f.sink.path, f.sink.line))


def _summary(flow):
    return (flow.vuln, flow.context, f"{flow.source.kind}.{flow.source.field_name}",
            [r.text() for r in flow.entry], [r.text() for r in flow.render], flow.stores,
            f"{flow.sink.path.rsplit('/', 1)[-1]}:{flow.sink.line}")


def test_node_board_stored_dom_xss_crosses_server_db_api_and_browser(fixtures):
    """POST 본문 → INSERT → SELECT → GET API 응답 → 브라우저 fetch → innerHTML. 파일 네 개에 걸친 저장형 흐름 한 건."""
    flows = _flows(fixtures / "poly" / "node-board")
    assert [_summary(f) for f in flows] == [
        ("xss", "html_text", "body.content", ["POST /notes"], ["GET /notes/:param"], ("notes",), "app.js:205")]
    files = [s.path.rsplit("/", 1)[-1] for s in flows[0].steps]
    assert files[0] == "server.mjs" and "storage.mjs" in files and "http.mjs" in files and files[-1] == "app.js"


def test_node_board_safe_rendering_has_no_flow(fixtures, tmp_path):
    root = tmp_path / "safe"
    shutil.copytree(fixtures / "poly" / "node-board", root)
    app = root / "frontend" / "public" / "app.js"
    app.write_text(app.read_text(encoding="utf-8").replace(
        "$('detail-body').innerHTML = note.content", "$('detail-body').textContent = note.content"), encoding="utf-8")
    assert _flows(root) == []


def test_java_stored_and_reflected_xss_and_sanitized_route(fixtures):
    flows = _flows(fixtures / "poly" / "java-board")
    assert [_summary(f) for f in flows] == [
        ("xss", "html_text", "body.body", ["POST /posts"], ["GET /posts/:param"], ("posts",), "BoardController.java:26"),
        ("xss", "html_text", "query.None", ["GET /hello"], ["GET /hello"], (), "BoardController.java:32"),
    ]  # title 열은 조회하지 않으므로 흐름이 없고, htmlEscape를 거친 /safe도 없다


def test_go_reflected_xss_and_sql_injection(fixtures):
    flows = _flows(fixtures / "poly" / "go-hello")
    assert [_summary(f) for f in flows] == [
        ("xss", "html_text", "query.None", ["GET /hello"], ["GET /hello"], (), "main.go:14"),
        ("sqli", "sql", "query.None", ["GET /search"], ["GET /search"], (), "main.go:24"),
    ]  # html.EscapeString을 거친 /safe는 없다


def test_scan_emits_sarif_dast_target_for_polyglot_flow(fixtures):
    result = scan(fixtures / "poly" / "node-board")
    sarif = result.sarif(run_id="poly-1", commit="a" * 40)
    run = sarif["runs"][0]
    stored = [r for r in run["results"] if r["ruleId"] == "delta.xss.stored"]
    assert len(stored) == 1 and stored[0]["kind"] == "fail"
    dast = stored[0]["properties"]["dast"]
    assert dast["verifiable"] and dast["field"] == "content" and dast["stored"]
    assert dast["entry"] == {"method": "POST", "path": "/notes"} and dast["render"] == {"method": "GET", "path": "/notes/:param"}
    assert run["properties"]["dast_targets"] == 1
    flow = stored[0]["codeFlows"][0]["threadFlows"][0]["locations"]
    assert flow[0]["location"]["physicalLocation"]["artifactLocation"]["uri"] == "backend/server.mjs"
    assert flow[-1]["location"]["physicalLocation"]["artifactLocation"]["uri"] == "frontend/public/app.js"


def test_secret_rules_cover_other_languages(tmp_path):
    (tmp_path / "a.js").write_text('const apiKey = "sk-live-1234567890abcdef";\n', encoding="utf-8")
    (tmp_path / "B.java").write_text('class B { String password = "hunter2-prod-value"; }\n', encoding="utf-8")
    (tmp_path / "c.go").write_text('package c\n\nfunc f() { token := "tok-prod-123456" ; _ = token }\n', encoding="utf-8")
    _, secrets = locate(tmp_path)
    assert {s.loc.path for s in secrets} == {"a.js", "B.java", "c.go"}


@pytest.mark.parametrize("text, prefix, expected", [
    ("`${config.apiEndpoint}/notes`", False, "/notes"),
    ("`${config.apiEndpoint}/notes/`", True, "/notes/:param"),
    ("`/notes/${encodeURIComponent(route.id)}`", False, "/notes/:param"),
    ('"/posts/{id}"', False, "/posts/:param"),
    ("'/users/:id?tab=1'", False, "/users/:param"),
])
def test_route_normalization(text, prefix, expected):
    assert norm_url(text, prefix) == expected


def test_sql_and_argument_helpers():
    assert written("INSERT INTO delta_notes.notes (id, title, content) VALUES ($1, $2, $3)") == ("notes", ["id", "title", "content"])
    assert written("UPDATE posts SET body = ?, title = ? WHERE id = ?") == ("posts", ["body", "title"])
    assert read_columns('SELECT id, content AS "c" FROM notes') == {"id", "content"}
    assert read_columns("SELECT * FROM notes") is None
    assert [("a, [b, c], 'd,e'")[a:b].strip() for a, b in split_args("a, [b, c], 'd,e'")] == ["a", "[b, c]", "'d,e'"]
    assert params_of("res, status, data, headers = {}") == ("res", "status", "data", "headers")
    assert params_of("@RequestParam String title, int n") == ("title", "n")
    assert params_of("w http.ResponseWriter, r *http.Request", "leading") == ("w", "r")
