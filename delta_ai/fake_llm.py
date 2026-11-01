"""가짜 LLM — 키 없이 팀 연동과 데모 흐름을 맞추기 위한 고정 응답. 실제 분석을 하지 않는다."""

from __future__ import annotations

import json

from delta_ai.judge import JudgeRequest
from delta_ai.llm import LlmResponse, LlmRequest

MODEL = "fake-llm"
_DIFF_HEADER = "--- a/server.js\n+++ b/server.js\n"
_SQLI_HUNK = """@@ -9 +9 @@
-  const result = await pool.query("SELECT id, name FROM users WHERE name = '" + req.query.name + "'");
+  const result = await pool.query("SELECT id, name FROM users WHERE name = $1", [req.query.name]);
"""
_CMDI_HUNK = """@@ -14 +14,5 @@
-  res.send(await runDiag(req.query.host));
+  const host = String(req.query.host ?? "");
+  if (!/^[A-Za-z0-9.-]{1,253}$/.test(host)) {
+    return res.status(400).send("invalid host");
+  }
+  res.send(await runDiag(host));
"""
_CLASSIFICATION = {"CWE-89": "true_positive", "CWE-78": "true_positive", "CWE-79": "false_positive"}
_HUNKS = {"CWE-89": _SQLI_HUNK, "CWE-78": _CMDI_HUNK}
_DETECTORS = {"CWE-78": ["runDiag(", "req.query"]}


def _patches(findings) -> list[dict[str, object]]:
    covered = [f for f in findings if f.rule_id in _HUNKS]
    if not covered:
        return []
    hunks = "".join(_HUNKS[f.rule_id] for f in sorted(covered, key=lambda f: int(f.location.get("line", 0))))
    return [{"path": "server.js", "diff": _DIFF_HEADER + hunks, "finding_ids": [f.finding_id for f in covered]}]


class FakeLLM:
    model = MODEL

    async def complete(self, request: LlmRequest) -> LlmResponse:
        if not isinstance(request, JudgeRequest):
            raise TypeError("FakeLLM 은 JudgeRequest 만 처리한다")
        security = request.kind == "code_security"
        items = [{
            "finding_id": finding.finding_id,
            "why": f"[가짜 LLM] {finding.title}: {finding.evidence}",
            "cited_rule_ids": [finding.rule_id] if finding.rule_id in request.doc_rule_ids else [],
            "classification": _CLASSIFICATION.get(finding.rule_id, "uncertain") if security else None,
            "detector_contains": _DETECTORS.get(finding.rule_id, []) if request.kind == "feedback_ingest" else [],
        } for finding in request.findings]
        patches = _patches(request.findings) if security else []
        return LlmResponse(text=json.dumps({"items": items, "patches": patches}, ensure_ascii=False), model=MODEL)
