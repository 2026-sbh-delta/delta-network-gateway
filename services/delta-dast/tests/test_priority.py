"""조치 우선순위: LLM 응답을 그대로 믿지 않고 항목·순번을 검사하는지, 판단 근거가 될 사실을 넘기는지."""

import pytest

from dast_agent.contracts import Finding
from dast_agent.priority import check, facts

FINDING = Finding.model_validate({
    "id": "sast-a", "title": "댓글 저장형 XSS", "cwe": "CWE-79", "severity": "HIGH",
    "description": "form.comment가 필터 없이 GET /posts/<post_id>/comments에 출력", "source_file": "app/comments.py",
    "line": 16,
})


def item(fid, rank, level="high"):
    return {"finding_id": fid, "rank": rank, "level": level, "rationale": "근거"}


def test_ranked_items_come_back_in_order():
    value = check({"summary": "요약", "items": [item("b", 2, "low"), item("a", 1, "urgent")]}, ["a", "b"])
    assert [i["finding_id"] for i in value["items"]] == ["a", "b"]


@pytest.mark.parametrize("items", [
    [item("a", 1)],                       # 빠진 항목
    [item("a", 1), item("x", 2)],         # 없는 항목
    [item("a", 1), item("b", 1)],         # 순번 중복
    [item("a", 1), item("b", 3)],         # 순번 건너뜀
])
def test_incomplete_ranking_is_rejected(items):
    with pytest.raises(RuntimeError):
        check({"summary": "요약", "items": items}, ["a", "b"])


def test_facts_carry_csp_block_and_code_location():
    result = {"status": "BLOCKED", "message": "CSP가 실행을 차단했습니다.",
              "evidence": {"executed": [], "rendered": ["x"], "csp_blocked": True,
                           "console": ["Refused to execute inline event handler because it violates Content Security Policy"]}}
    row = facts(FINDING, result)
    assert row["dast_status"] == "BLOCKED" and row["csp_blocked"] and not row["executed"]
    assert row["code"] == "app/comments.py:16"
    assert "Content Security Policy" in row["csp_console"]
