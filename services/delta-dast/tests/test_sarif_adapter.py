"""Delta SAST SARIF 입력 변환."""

import copy
import json
from pathlib import Path

import pytest

from dast_agent.adapters import from_sarif, is_sarif, normalize_report
from dast_agent.contracts import Report

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "delta-sast-board.sarif"


@pytest.fixture
def sarif():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_sarif_becomes_internal_report(sarif):
    report = Report.model_validate(normalize_report(sarif))
    assert report.source == "delta-sast"
    fails = [r for r in sarif["runs"][0]["results"] if r["kind"] == "fail"]
    assert len(report.findings) == len(fails) == 3
    ids = {f.id for f in report.findings}
    assert ids == {"sast-" + r["partialFingerprints"]["deltaFlow/v1"] for r in fails}
    body = next(f for f in report.findings if "form.body" in f.title)
    assert body.title.startswith("저장형 XSS") and body.cwe == "CWE-79" and body.severity == "HIGH"
    assert (body.source_file, body.line) == ("app/board.py", 52)
    for hint in ("입력 라우트: POST /posts", "입력 칸: form.body", "출력 라우트: GET /posts/<post_id>",
                 "출력 맥락: html_text", "app/store.py:10"):
        assert hint in body.description
    assert body.trigger is None and body.payload is None


def test_stored_findings_come_first(sarif):
    titles = [f["title"] for f in from_sarif(sarif)["findings"]]
    assert titles[-1].startswith("반사형")


def test_pass_unverifiable_and_non_xss_results_are_skipped(sarif):
    raw = copy.deepcopy(sarif)
    for result in raw["runs"][0]["results"]:
        result["kind"] = "pass"
    with pytest.raises(ValueError):
        from_sarif(raw)
    raw = copy.deepcopy(sarif)
    raw["runs"][0]["results"][0]["ruleId"] = "delta.sqli"
    raw["runs"][0]["results"][1]["properties"]["dast"]["verifiable"] = False
    kept = {f["id"] for f in from_sarif(raw)["findings"]}
    dropped = {"sast-" + raw["runs"][0]["results"][i]["partialFingerprints"]["deltaFlow/v1"] for i in (0, 1)}
    assert not kept & dropped


def test_internal_and_aws_formats_are_untouched():
    internal = {"source": "ai-sast", "source_label": "x", "findings": [{"id": "a"}]}
    assert not is_sarif(internal) and normalize_report(internal) is internal


def test_sarif_code_flow_is_passed_for_the_ui(sarif):
    report = Report.model_validate(normalize_report(sarif))
    body = next(f for f in report.findings if "form.body" in f.title)
    roles = [s.role for s in body.code_flow]
    assert roles[0] == "source" and roles[-1] == "sink" and set(roles[1:-1]) <= {"propagation"}
    assert (body.code_flow[0].file, body.code_flow[-1].file, body.code_flow[-1].line) == ("app/board.py", "app/board.py", 52)
    assert any(s.file == "app/store.py" and s.note.startswith("저장") for s in body.code_flow)
    assert len(body.code_flow) <= 8
