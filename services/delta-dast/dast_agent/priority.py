"""SAST 근거(코드 흐름)와 DAST 결과(실제 실행 여부)를 함께 보고 조치 우선순위와 근거를 정한다."""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time

from .contracts import Finding

LEVELS = ("urgent", "high", "medium", "low")


def model_name() -> str:
    # 결과 몇 건을 비교해 순서를 매기는 짧은 판단이라 작은 모델로 충분하다.
    model = os.environ.get("DAST_PRIORITY_MODEL", "claude-haiku-5-5")
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,120}", model):
        raise RuntimeError("DAST_PRIORITY_MODEL 형식이 유효하지 않습니다.")
    return model

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "items": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "finding_id": {"type": "string"},
                "rank": {"type": "integer"},
                "level": {"type": "string", "enum": list(LEVELS)},
                "rationale": {"type": "string"},
            },
            "required": ["finding_id", "rank", "level", "rationale"],
            "additionalProperties": False,
        }},
    },
    "required": ["summary", "items"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """너는 보안 담당자다. 같은 앱에서 나온 취약점들의 조치 순서를 정한다.
근거는 두 가지다. SAST는 코드에서 입력이 필터 없이 출력까지 가는 흐름을 보여 준다. DAST는 실제 브라우저에서 그 입력이 실행됐는지 보여 준다.
- REPRODUCED: 실행이 확인됐다. 지금 악용할 수 있다.
- BLOCKED: 코드는 취약하지만 CSP 같은 방어 계층이 실행을 막았다. 당장 영향은 낮지만 방어 계층이 바뀌면 바로 악용된다.
- NOT_REPRODUCED: 출력됐지만 실행되지 않았다.
- INCONCLUSIVE: 판정하지 못했다. 우선순위를 높게 매기지 말고 재점검이 필요하다고 적는다.
입력에 있는 사실(경로, 실행 여부, 차단 이유, 저장형 여부, 심각도)만 근거로 쓴다. 입력에 없는 내용은 추측하지 않는다.
rank는 1부터 겹치지 않게 매긴다. level은 urgent·high·medium·low 중 하나다.
rationale은 한국어 2~3문장으로, 왜 이 순서인지 다른 항목과 비교해 쓴다. summary는 한국어 한 문장이다."""


def facts(finding: Finding, result: dict) -> dict:
    ev = result.get("evidence") or {}
    return {
        "finding_id": finding.id, "title": finding.title, "cwe": finding.cwe, "severity": finding.severity,
        "code": f"{finding.source_file}:{finding.line}", "sast": finding.description[:700],
        "dast_status": result["status"], "dast_message": result.get("message", "")[:300],
        "executed": bool(ev.get("executed")), "rendered": bool(ev.get("rendered")),
        "csp_blocked": bool(ev.get("csp_blocked")),
        "csp_console": (ev.get("console") or [""])[0][:200] if ev.get("csp_blocked") else "",
    }


def check(value: dict, ids: list[str]) -> dict:
    items = value.get("items") or []
    if sorted(item["finding_id"] for item in items) != sorted(ids):
        raise RuntimeError("우선순위에 빠지거나 없는 항목이 있습니다.")
    if sorted(item["rank"] for item in items) != list(range(1, len(ids) + 1)):
        raise RuntimeError("우선순위 순번이 1부터 이어지지 않습니다.")
    return {"summary": value["summary"][:300],
            "items": sorted(({**item, "rationale": item["rationale"][:600]} for item in items),
                            key=lambda item: item["rank"])}


async def prioritize(findings: list[Finding], results: list[dict]) -> dict:
    from claude_agent_sdk import ClaudeAgentOptions, PermissionResultDeny, ResultMessage, query

    async def deny(*_args):
        return PermissionResultDeny(message="우선순위 판단에서는 도구를 쓰지 않는다.")

    by_id = {result["finding_id"]: result for result in results}
    rows = [facts(finding, by_id[finding.id]) for finding in findings if finding.id in by_id]
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="dast-priority-") as directory:
        options = ClaudeAgentOptions(
            model=model_name(), tools=[], allowed_tools=[], setting_sources=[], skills=[],
            plugins=[], mcp_servers={}, strict_mcp_config=True, can_use_tool=deny, cwd=directory,
            max_turns=2, max_budget_usd=0.3, extra_args={"no-session-persistence": None},
            stderr=lambda _line: None, system_prompt=SYSTEM_PROMPT, effort="low",
            output_format={"type": "json_schema", "schema": SCHEMA},
        )
        result = None
        async with asyncio.timeout(30):
            async for message in query(prompt=json.dumps(rows, ensure_ascii=False), options=options):
                if isinstance(message, ResultMessage):
                    result = message
    if result is None or result.is_error or result.structured_output is None:
        raise RuntimeError("우선순위를 판단하지 못했습니다.")
    value = result.structured_output
    ranked = check(json.loads(value) if isinstance(value, str) else dict(value), [row["finding_id"] for row in rows])
    return ranked | {"model": model_name(), "duration_ms": round((time.monotonic() - started) * 1000),
                     "cost_usd": getattr(result, "total_cost_usd", None)}
