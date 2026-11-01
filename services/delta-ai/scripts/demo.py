"""시연 흐름을 터미널로 확인한다. python scripts/demo.py [fake|none|claude]"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from delta_ai.contract import AssessmentRequest, AssessmentResult
from delta_ai.evaluation import FIXTURES, build_request
from delta_ai.fake_llm import FakeLLM
from delta_ai.judge import LlmOutput
from delta_ai.llm import ClaudeLLM
from delta_ai.service import assess
from delta_ai.store import RuleStore

LIGHT = {"green": "🟢", "red": "🔴", "unknown": "🟡"}


def _llm(mode: str):
    if mode == "claude":
        return ClaudeLLM(output=LlmOutput)
    return FakeLLM() if mode == "fake" else None


def _show(title: str, result: AssessmentResult) -> None:
    print(f"\n── {title}\n판정: {result.verdict} {list(result.reasons)}")
    for check in result.checks:
        print(f"  {LIGHT[check.state]} {check.name}")
    for finding in result.findings:
        if finding.severity != "low":
            print(f"  · [{finding.status}] {finding.rule_id} {finding.evidence[:90]}")
    for rule in result.remediation.waf_rules:
        print(f"  → {rule.action} {rule.expression}")
    for candidate in result.rule_candidates:
        print(f"  → 규칙 후보 {candidate.candidate_id} {candidate.status} {candidate.detector_contains} {candidate.regression}")


async def main(mode: str) -> None:
    llm = _llm(mode)
    with tempfile.TemporaryDirectory() as tmp:
        store = RuleStore(Path(tmp) / "rules.json")
        _show("① AWS→GCP: 대상에 WAF 없음, 공격 통과", await assess(build_request({"base": "parity-waf-missing.json"}), llm=llm, store=store))
        _show("② 수정 적용 뒤 재시험: 공격 차단", await assess(build_request({"base": "parity-ok.json"}), llm=llm, store=store))
        report = AssessmentRequest.model_validate(json.loads((FIXTURES / "feedback-report.json").read_text(encoding="utf-8")))
        ingest = await assess(report, llm=llm, store=store)
        _show("③ 긴 리뷰 보고서 수집: 규칙 후보", ingest)
        for candidate in ingest.rule_candidates:
            if candidate.status == "proposed" and candidate.regression.ready:
                store.decide(candidate.candidate_id, decision="approved", approver="demo")
        print(f"\n── ④ 규칙 승인: 활성 규칙 {[rule.candidate_id for rule in store.active_rules()]}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "fake"))
