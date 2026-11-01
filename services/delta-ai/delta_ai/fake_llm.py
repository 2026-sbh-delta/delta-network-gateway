"""가짜 LLM — 키 없이 팀 연동과 데모 흐름을 맞추기 위한 고정 응답. 실제 분석을 하지 않는다."""

from __future__ import annotations

import json

from delta_ai.judge import JudgeRequest
from delta_ai.llm import LlmResponse, LlmRequest

MODEL = "fake-llm"
_DETECTORS = {"CWE-78": ["runDiag(", "req.query"]}


class FakeLLM:
    model = MODEL

    async def complete(self, request: LlmRequest) -> LlmResponse:
        if not isinstance(request, JudgeRequest):
            raise TypeError("FakeLLM 은 JudgeRequest 만 처리한다")
        items = [{
            "finding_id": finding.finding_id,
            "why": f"[가짜 LLM] {finding.title}: {finding.evidence}",
            "cited_rule_ids": [finding.rule_id] if finding.rule_id in request.doc_rule_ids else [],
            "detector_contains": _DETECTORS.get(finding.rule_id, []) if request.kind == "feedback_ingest" else [],
        } for finding in request.findings]
        return LlmResponse(text=json.dumps({"items": items}, ensure_ascii=False), model=MODEL)
