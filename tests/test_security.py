from __future__ import annotations

import copy
import json
import subprocess

import pytest

from delta_ai.contract import AssessmentRequest
from delta_ai.fake_llm import FakeLLM
from delta_ai.llm import LlmResponse, LlmUnavailable
from delta_ai.security import check_security, normalize_diff, parse_sarif, validate_diff
from delta_ai.service import assess
from delta_ai.store import RuleStore


class ScriptedLLM:
    model = "scripted"

    def __init__(self, items, patches=()):
        self.items = items
        self.patches = list(patches)
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        return LlmResponse(text=json.dumps({"items": self.items, "patches": self.patches}), model=self.model)


def test_parse_real_semgrep_sarif_maps_cwe_and_location(security_request):
    findings = parse_sarif(security_request["evidence"]["sarif"])

    assert {f.rule_id for f in findings} == {"CWE-89", "CWE-79"}
    sql = next(f for f in findings if f.rule_id == "CWE-89")
    assert sql.location["path"] == "server.js"
    assert sql.location["line"] == "9"
    assert sql.severity == "high"


def test_sarif_result_without_cwe_keeps_tool_rule_id_and_is_unknown():
    sarif = {"runs": [{"tool": {"driver": {"rules": [{"id": "x.y"}]}}, "results": [{
        "ruleId": "x.y", "level": "error", "message": {"text": "m"},
        "locations": [{"physicalLocation": {"artifactLocation": {"uri": "a.js"}, "region": {"startLine": 1}}}],
    }]}]}

    findings = parse_sarif(sarif)

    assert findings[0].rule_id == "x.y"


async def test_true_positive_with_valid_diff_returns_fix(security_request, tmp_path):
    result = await assess(AssessmentRequest.model_validate(security_request), llm=FakeLLM(),
                          store=RuleStore(tmp_path / "rules.json"))

    assert result.verdict == "fix"
    diff = result.remediation.code_diffs[0]
    assert diff.path == "server.js"
    assert "pool.query" in diff.diff
    assert result.meta.llm_used is True


async def test_no_llm_makes_security_finding_needs_human(security_request, tmp_path):
    result = await assess(AssessmentRequest.model_validate(security_request), llm=None,
                          store=RuleStore(tmp_path / "rules.json"))

    assert result.verdict == "needs_human"
    assert "LLM_UNAVAILABLE" in result.reasons


async def test_llm_cannot_dismiss_high_severity_finding(security_request, tmp_path):
    raw = copy.deepcopy(security_request)
    req = AssessmentRequest.model_validate(raw)
    findings = check_security(req, active_rules=()).findings
    items = [{"finding_id": f.finding_id, "why": "오탐이다", "cited_rule_ids": [f.rule_id],
              "classification": "false_positive", "code_diff": None, "detector_contains": []} for f in findings]

    result = await assess(req, llm=ScriptedLLM(items), store=RuleStore(tmp_path / "rules.json"))

    assert result.verdict == "needs_human"
    assert next(f for f in result.findings if f.rule_id == "CWE-89").status == "unknown"


async def test_llm_may_dismiss_medium_severity_finding(security_request, tmp_path):
    raw = copy.deepcopy(security_request)
    req = AssessmentRequest.model_validate(raw)
    findings = check_security(req, active_rules=()).findings
    items = [{"finding_id": f.finding_id, "why": "오탐이다", "cited_rule_ids": [f.rule_id],
              "classification": "false_positive", "code_diff": None, "detector_contains": []} for f in findings]

    result = await assess(req, llm=ScriptedLLM(items), store=RuleStore(tmp_path / "rules.json"))

    assert next(f for f in result.findings if f.rule_id == "CWE-79").status == "dismissed"


async def test_invented_citation_is_rejected(security_request, tmp_path):
    req = AssessmentRequest.model_validate(security_request)
    findings = check_security(req, active_rules=()).findings
    items = [{"finding_id": f.finding_id, "why": "설명", "cited_rule_ids": ["CWE-999"],
              "classification": "true_positive", "code_diff": None, "detector_contains": []} for f in findings]

    result = await assess(req, llm=ScriptedLLM(items), store=RuleStore(tmp_path / "rules.json"))

    assert result.verdict == "needs_human"
    assert "LLM_OUTPUT_INVALID" in result.reasons


async def test_prompt_masks_secrets_and_marks_code_untrusted(security_request, tmp_path):
    raw = copy.deepcopy(security_request)
    token = "ghp_" + "a" * 30
    raw["evidence"]["code_files"]["server.js"] = raw["evidence"]["code_files"]["server.js"].replace(
        'const app = express();', f'const app = express(); const t = "{token}";')
    # SARIF 스니펫에도 넣어 프롬프트 경로 전체를 확인한다
    raw["evidence"]["sarif"]["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["region"]["snippet"]["text"] = token
    llm = ScriptedLLM([])

    await assess(AssessmentRequest.model_validate(raw), llm=llm, store=RuleStore(tmp_path / "rules.json"))

    assert token not in llm.requests[0].user
    assert "<untrusted_data>" in llm.requests[0].user


def test_validate_diff_accepts_small_patch_to_finding_file():
    diff = "--- a/server.js\n+++ b/server.js\n@@ -1 +1 @@\n-old\n+new\n"

    assert validate_diff(diff, "server.js") is None


def test_validate_diff_rejects_other_file():
    diff = "--- a/.github/workflows/ci.yml\n+++ b/.github/workflows/ci.yml\n@@ -1 +1 @@\n-old\n+new\n"

    assert validate_diff(diff, "server.js") == "OTHER_FILE"


def test_validate_diff_rejects_second_file_hidden_in_patch():
    diff = ("--- a/server.js\n+++ b/server.js\n@@ -1 +1 @@\n-old\n+new\n"
            "--- a/deploy.yaml\n+++ b/deploy.yaml\n@@ -1 +1 @@\n-a\n+b\n")

    assert validate_diff(diff, "server.js") == "OTHER_FILE"


def test_validate_diff_rejects_secret_in_patch():
    diff = f"--- a/server.js\n+++ b/server.js\n@@ -1 +1 @@\n-old\n+const k = 'ghp_{'a' * 30}';\n"

    assert validate_diff(diff, "server.js") == "SECRET_IN_DIFF"


def test_validate_diff_rejects_oversized_patch():
    body = "".join(f"+line{i}\n" for i in range(100))

    assert validate_diff(f"--- a/server.js\n+++ b/server.js\n@@ -1 +1,100 @@\n{body}", "server.js") == "TOO_LARGE"


def test_validate_diff_rejects_non_diff_text():
    assert validate_diff("그냥 설명", "server.js") == "NOT_A_DIFF"


async def test_unavailable_llm_is_logged_and_reported(security_request, tmp_path, caplog):
    class Broken:
        model = "broken"

        async def complete(self, request):
            raise LlmUnavailable("401")

    result = await assess(AssessmentRequest.model_validate(security_request), llm=Broken(),
                          store=RuleStore(tmp_path / "rules.json"))

    assert "LLM_UNAVAILABLE" in result.reasons
    assert result.meta.llm_used is False
    assert "LLM 을 쓰지 못해" in caplog.text


async def test_prompt_includes_related_files_with_secrets_redacted(security_request, tmp_path):
    token = "ghp_" + "b" * 30
    security_request["evidence"]["code_files"]["diag.js"] += f'\nconst t = "{token}";\n'
    llm = ScriptedLLM([])

    await assess(AssessmentRequest.model_validate(security_request), llm=llm, store=RuleStore(tmp_path / "rules.json"))

    assert "promisify(exec)" in llm.requests[0].user
    assert token not in llm.requests[0].user


async def test_oversized_related_file_is_left_out_of_prompt(security_request, tmp_path):
    security_request["evidence"]["code_files"]["bundle.js"] = "unique_big_marker " * 2000
    llm = ScriptedLLM([])

    await assess(AssessmentRequest.model_validate(security_request), llm=llm, store=RuleStore(tmp_path / "rules.json"))

    assert "unique_big_marker" not in llm.requests[0].user


SQLI_LINE = """  const result = await pool.query("SELECT id, name FROM users WHERE name = '" + req.query.name + "'");"""


def _true_positive_items(findings):
    return [{"finding_id": f.finding_id, "why": "취약", "cited_rule_ids": [f.rule_id],
             "classification": "true_positive", "detector_contains": []} for f in findings]


def _sqli_diff(old_line=SQLI_LINE):
    return f"--- a/server.js\n+++ b/server.js\n@@ -9 +9 @@\n-{old_line}\n+  fixed();\n"


async def test_one_patch_can_cover_several_findings_and_appears_once(security_request, tmp_path):
    req = AssessmentRequest.model_validate(security_request)
    findings = check_security(req, active_rules=()).findings
    patch = {"path": "server.js", "diff": _sqli_diff(), "finding_ids": [f.finding_id for f in findings]}

    result = await assess(req, llm=ScriptedLLM(_true_positive_items(findings), [patch]),
                          store=RuleStore(tmp_path / "rules.json"))

    assert result.verdict == "fix"
    assert len(result.remediation.code_diffs) == 1


async def test_two_patches_for_one_file_are_rejected(security_request, tmp_path):
    req = AssessmentRequest.model_validate(security_request)
    findings = check_security(req, active_rules=()).findings
    patches = [{"path": "server.js", "diff": _sqli_diff(), "finding_ids": [f.finding_id]} for f in findings]

    result = await assess(req, llm=ScriptedLLM(_true_positive_items(findings), patches),
                          store=RuleStore(tmp_path / "rules.json"))

    assert result.verdict == "needs_human"
    assert "PATCH_REJECTED" in result.reasons
    assert result.remediation.code_diffs == ()


async def test_patch_with_invented_context_is_rejected(security_request, tmp_path):
    req = AssessmentRequest.model_validate(security_request)
    findings = check_security(req, active_rules=()).findings
    patch = {"path": "server.js", "diff": _sqli_diff("  없는 코드();"), "finding_ids": [f.finding_id for f in findings]}

    result = await assess(req, llm=ScriptedLLM(_true_positive_items(findings), [patch]),
                          store=RuleStore(tmp_path / "rules.json"))

    assert "PATCH_REJECTED" in result.reasons


async def test_patch_for_finding_in_other_file_is_rejected(security_request, tmp_path):
    req = AssessmentRequest.model_validate(security_request)
    findings = check_security(req, active_rules=()).findings
    diff = "--- a/diag.js\n+++ b/diag.js\n@@ -1 +1 @@\n-const { exec } = require(\"child_process\");\n+x\n"
    patch = {"path": "diag.js", "diff": diff, "finding_ids": [f.finding_id for f in findings]}

    result = await assess(req, llm=ScriptedLLM(_true_positive_items(findings), [patch]),
                          store=RuleStore(tmp_path / "rules.json"))

    assert "PATCH_REJECTED" in result.reasons


def _git_apply(tmp_path, name, text, diff):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / name).write_text(text)
    subprocess.run(["git", "apply", "-"], cwd=tmp_path, input=diff, text=True, check=True, capture_output=True)
    return (tmp_path / name).read_text()


def test_normalized_diff_applies_with_plain_git_despite_wrong_numbers(tmp_path):
    text = "a\nb\nc\nd\ne\n"
    diff = "--- a/a.js\n+++ b/a.js\n@@ -99,3 +99,3 @@\n b\n-c\n+C\n\n"

    normalized = normalize_diff(diff, "a.js", text)

    assert _git_apply(tmp_path, "a.js", text, normalized) == "a\nb\nC\nd\ne\n"


def test_normalized_diff_handles_hunk_without_context(tmp_path):
    text = "a\nb\nc\nd\ne\nf\ng\nh\n"
    diff = "--- a/a.js\n+++ b/a.js\n@@ -2 +2 @@\n-b\n+B\n@@ -7 +7,2 @@\n-g\n+G\n+G2\n"

    normalized = normalize_diff(diff, "a.js", text)

    assert _git_apply(tmp_path, "a.js", text, normalized) == "a\nB\nc\nd\ne\nf\nG\nG2\nh\n"


def test_normalize_rejects_hunks_out_of_order():
    diff = "--- a/a.js\n+++ b/a.js\n@@ -3 +3 @@\n-c\n+C\n@@ -1 +1 @@\n-a\n+A\n"

    assert normalize_diff(diff, "a.js", "a\nb\nc\n") is None


def test_normalize_rejects_hunk_without_original_lines():
    assert normalize_diff("--- a/a.js\n+++ b/a.js\n@@ -0,0 +1 @@\n+new\n", "a.js", "a\n") is None


def test_normalize_rejects_patch_that_changes_nothing():
    assert normalize_diff("--- a/a.js\n+++ b/a.js\n@@ -1 +1 @@\n a\n", "a.js", "a\n") is None


@pytest.mark.parametrize("text", ["a\r\nb\r\n", "a\nb"])
def test_normalize_rejects_files_whose_line_endings_cannot_be_restored(text):
    assert normalize_diff("--- a/a.js\n+++ b/a.js\n@@ -1 +1 @@\n-a\n+A\n", "a.js", text) is None


async def test_patch_without_source_file_is_rejected(security_request, tmp_path):
    req = AssessmentRequest.model_validate({**security_request, "evidence": {
        "sarif": security_request["evidence"]["sarif"]}})
    findings = check_security(req, active_rules=()).findings
    patch = {"path": "server.js", "diff": _sqli_diff(), "finding_ids": [f.finding_id for f in findings]}

    result = await assess(req, llm=ScriptedLLM(_true_positive_items(findings), [patch]),
                          store=RuleStore(tmp_path / "rules.json"))

    assert "PATCH_REJECTED" in result.reasons
