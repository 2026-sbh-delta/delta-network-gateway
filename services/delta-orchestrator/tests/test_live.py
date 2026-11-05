"""진행 보기: 실행 기록에 남은 ID 로만 각 도구를 읽고, 화면용으로 줄여 넘긴다."""

import asyncio

import httpx
import pytest

from delta_orchestrator import live

DAST_ID = "70f84964-f8d6-58b4-b3e9-21e9ad519725"
BOARD = {"run_id": "r1", "nodes": [
    {"id": "build", "detail": {"build": 12}},
    {"id": "sast", "detail": {"run_id": "sast-run-1"}},
    {"id": "stage", "detail": {}},
    {"id": "dast", "detail": {"run_id": DAST_ID}},
]}
SARIF = {"runs": [{"results": [
    {"ruleId": "delta.secret", "kind": "pass", "level": "none", "message": {"text": "비밀값 아님"},
     "locations": [{"physicalLocation": {"artifactLocation": {"uri": "app/config.py"}, "region": {"startLine": 3}}}],
     "partialFingerprints": {"deltaFlow/v1": "s1"}, "properties": {"dast": {"verifiable": False}}},
    {"ruleId": "delta.xss.stored", "kind": "fail", "level": "error", "message": {"text": "저장형 XSS"},
     "locations": [{"physicalLocation": {"artifactLocation": {"uri": "app/templates/post.html"}, "region": {"startLine": 9}}}],
     "codeFlows": [{"threadFlows": [{"locations": [
         {"location": {"physicalLocation": {"artifactLocation": {"uri": "app/routes.py"}, "region": {"startLine": 20}},
                       "message": {"text": "request.form['body']"}}},
         {"location": {"physicalLocation": {"artifactLocation": {"uri": "app/templates/post.html"}, "region": {"startLine": 9}},
                       "message": {"text": "|safe 출력"}}}]}]}],
     "partialFingerprints": {"deltaFlow/v1": "abc"},
     "properties": {"dast": {"verifiable": True, "entry": {"method": "POST", "path": "/posts"}, "stored": True},
                    "judgment": {"by": "rule", "verdict": "vulnerable", "reason": "이스케이프 없음"}}},
]}]}


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def run(coro):
    return asyncio.run(coro)


def test_ids_come_only_from_the_run_record():
    assert live.jenkins_build(BOARD, "build") == ("delta-build", 12)
    with pytest.raises(live.LiveError):
        live.jenkins_build(BOARD, "stage")  # 아직 빌드 번호가 없다
    with pytest.raises(live.LiveError):
        live.jenkins_build(BOARD, "dast")  # 콘솔 로그가 없는 단계
    with pytest.raises(live.LiveError):
        live.dast_run({"nodes": [{"id": "dast", "detail": {"run_id": "../../etc"}}]})


def test_sarif_is_reduced_to_findings_with_paths_fail_first():
    findings = live.summarize_sarif(SARIF)
    assert [f["kind"] for f in findings] == ["fail", "pass"]
    top = findings[0]
    assert top["id"] == "sast-abc" and top["dast"] and top["stored"]
    assert top["sink"] == {"file": "app/templates/post.html", "line": 9}
    assert [s["note"] for s in top["steps"]] == ["request.form['body']", "|safe 출력"]


def test_jenkins_log_reads_progressively(monkeypatch):
    monkeypatch.setenv("JENKINS_PLATFORM_PASSWORD", "pw")
    seen = []

    def handler(request):
        seen.append(str(request.url))
        assert request.headers["authorization"].startswith("Basic ")
        return httpx.Response(200, text="Started by user \x1b[8mha:////4J2l26=\x1b[0mplatform\r\n\x1b[32m빌드 중\x1b[0m\n",
                              headers={"X-Text-Size": "120", "X-More-Data": "true"})

    out = run(live.jenkins_log(client(handler), BOARD, "build", 100))
    assert seen == ["http://jenkins.cp.internal:8080/job/delta-build/12/logText/progressiveText?start=100"]
    assert out == {"job": "delta-build", "build": 12, "text": "Started by user platform\n빌드 중\n", "next": 120, "more": True}


def test_dast_view_relinks_screenshots_through_this_page():
    def handler(request):
        assert request.url.path == f"/api/runs/{DAST_ID}/view"
        return httpx.Response(200, json={"run_id": DAST_ID, "status": "done", "live": False, "secret": "x",
                                         "sast_feedback": [{"fingerprint": "abc", "outcome": "ineffective", "detail": ""}],
                                         "results": [{"finding_id": "sast-abc", "status": "REPRODUCED",
                                                      "evidence": {"screenshots": [f"/api/artifacts/{DAST_ID}/sast-abc-after.png"]}}],
                                         "report": {"findings": [{"id": "sast-abc", "title": "저장형 XSS", "description": "긴 설명"}]}})

    out = run(live.dast_view(client(handler), BOARD, "/api/pipeline/runs/r1/live/dast"))
    assert "secret" not in out and out["findings"] == [{"id": "sast-abc", "title": "저장형 XSS"}]
    assert out["sast_feedback"][0]["outcome"] == "ineffective"  # SAST 회신 결과도 화면으로 넘긴다
    assert out["results"][0]["evidence"]["screenshots"] == ["/api/pipeline/runs/r1/live/dast/artifacts/sast-abc-after.png"]


def test_dast_artifact_only_png_names():
    def handler(request):
        return httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})

    assert run(live.dast_artifact(client(handler), BOARD, "sast-abc-after.png")) == b"\x89PNG"
    for bad in ("../x.png", "report.json", "a.png.html"):
        with pytest.raises(live.LiveError):
            run(live.dast_artifact(client(handler), BOARD, bad))
