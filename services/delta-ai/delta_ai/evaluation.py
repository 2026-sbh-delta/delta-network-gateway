"""평가셋 실행 — 픽스처에 변형을 합쳐 판정과 규칙을 대조한다. 테스트와 scripts/eval.py 가 같이 쓴다."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from delta_ai.contract import AssessmentRequest
from delta_ai.llm import LlmClient
from delta_ai.service import assess
from delta_ai.store import RuleStore

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"
CASES = ROOT / "eval" / "cases.yaml"


@dataclass(frozen=True)
class CaseResult:
    name: str
    passed: bool
    verdict: str
    reasons: tuple[str, ...]
    missing_rules: tuple[str, ...]
    usage: dict[str, Any]
    llm_used: bool


def _merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        merged[key] = _merge(base[key], value) if isinstance(value, dict) and isinstance(base.get(key), dict) else value
    return merged


def _security_request(code_replace: dict[str, list[str]]) -> dict[str, Any]:
    code_files = {path.name: path.read_text(encoding="utf-8") for path in (FIXTURES / "demo-app").glob("*.js")}
    for name, (old, new) in code_replace.items():
        code_files[name] = code_files[name].replace(old, new)
    sarif = json.loads((FIXTURES / "demo-app.sarif.json").read_text(encoding="utf-8"))
    return {"assessment_id": "eval-security", "kind": "code_security", "service": {"service_id": "demo-app"},
            "evidence": {"sarif": sarif, "code_files": code_files}}


def build_request(case: dict[str, Any]) -> AssessmentRequest:
    if case["base"] == "security":
        return AssessmentRequest.model_validate(_security_request(case.get("code_replace", {})))
    base = json.loads((FIXTURES / case["base"]).read_text(encoding="utf-8"))
    return AssessmentRequest.model_validate(_merge(copy.deepcopy(base), case.get("patch", {})))


def load_cases() -> list[dict[str, Any]]:
    return yaml.safe_load(CASES.read_text(encoding="utf-8"))["cases"]


async def run_case(case: dict[str, Any], *, llm: LlmClient | None, store_dir: Path) -> CaseResult:
    store = RuleStore(store_dir / f"{case['name']}.json")
    result = await assess(build_request(case), llm=llm, store=store)
    found = {f.rule_id for f in result.findings}
    missing = tuple(rule for rule in case.get("rules", []) if rule not in found)
    return CaseResult(name=case["name"], passed=result.verdict in case["expect"] and not missing,
                      verdict=result.verdict, reasons=result.reasons, missing_rules=missing, usage=result.meta.usage,
                      llm_used=result.meta.llm_used)
