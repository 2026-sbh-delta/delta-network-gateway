"""공식 Computer Use 툴셋(computer_toolset_20260801) 드라이버. Claude API 인증이 필요하다.

Claude Agent SDK는 Anthropic 정의 도구를 선언할 수 없으므로, 이 모드는 Messages API로
직접 루프를 돈다. 툴셋 멤버는 ProbeBrowser 조작으로 실행하고, 검사 전용 동작은
사용자 정의 도구(type_probe, new_session, navigate, finish)로 함께 제공한다.
"""

import asyncio
import json
import os

from .agent import SYSTEM_PROMPT, agent_prompt, model_name, playwright_key, scroll_delta
from .contracts import Finding
from .probe_browser import MAX_ACTIONS, ActionError, ProbeBrowser
from .registry import ResolvedTarget


TOOLSET = "computer"
# 구현하지 않은 멤버는 모델에 노출하지 않는다.
DISABLED_MEMBERS = ["zoom", "left_click_drag", "left_mouse_down", "left_mouse_up",
                    "hold_key", "right_click", "middle_click"]
HALT_TEXT = "Not executed: an earlier computer action in this turn failed."
# claude-sonnet-5-5 기준 단가(USD / 1M tokens). 비용 표시는 참고값이다.
PRICE_IN, PRICE_OUT = 2.0, 10.0


def custom_tools(canaries: list[str]) -> list[dict]:
    reason = {"type": "string", "description": "이 행동을 하는 이유(한국어 한 문장)"}
    return [
        {"name": "type_marker", "strict": True,
         "description": "포커스된 입력란에 출력 위치 확인용 반사 마커를 입력한다(실행되는 코드 없음).",
         "input_schema": {"type": "object", "additionalProperties": False,
                          "properties": {"reason": reason}, "required": ["reason"]}},
        {"name": "type_canary", "strict": True,
         "description": "반사 위치에 맞는 등록 카나리를 입력한다. canary_candidates 중에서 고른다.",
         "input_schema": {"type": "object", "additionalProperties": False,
                          "properties": {"canary_id": {"type": "string", "enum": canaries}, "reason": reason},
                          "required": ["canary_id", "reason"]}},
        {"name": "type_impact", "strict": True,
         "description": "impact_allowed일 때 영향 시연 페이로드를 입력한다. 탐지와 시연을 한 번에 한다.",
         "input_schema": {"type": "object", "additionalProperties": False,
                          "properties": {"reason": reason}, "required": ["reason"]}},
        {"name": "new_session", "strict": True,
         "description": "브라우저 세션을 버리고 새 세션으로 시작 화면을 연다. 저장형 재현 확인용.",
         "input_schema": {"type": "object", "additionalProperties": False,
                          "properties": {"reason": reason}, "required": ["reason"]}},
        {"name": "navigate", "strict": True,
         "description": "허용된 대상 안의 경로로 이동한다. 예: /board",
         "input_schema": {"type": "object", "additionalProperties": False,
                          "properties": {"path": {"type": "string"}, "reason": reason},
                          "required": ["path", "reason"]}},
        {"name": "finish", "strict": True, "description": "검사를 끝낸다.",
         "input_schema": {"type": "object", "additionalProperties": False, "properties": {
             "conclusion": {"type": "string", "enum": ["executed", "not_executed", "could_not_reach"]},
             "reason": {"type": "string"}}, "required": ["conclusion", "reason"]}},
    ]


def _image(data: str) -> list[dict]:
    return [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}]


async def _run_member(browser: ProbeBrowser, name: str, args: dict) -> list[dict] | str:
    """툴셋 멤버 하나를 실행한다. 화면이 필요한 멤버만 이미지를 돌려준다."""
    if name == "screenshot":
        return _image(await browser.screenshot())
    if name in {"left_click", "double_click", "triple_click"}:
        x, y = args.get("coordinate") or browser.mouse
        count = {"left_click": 1, "double_click": 2, "triple_click": 3}[name]
        await browser.record(name, "", f"({x}, {y})")
        await browser.click(int(x), int(y), count=count)
        return "OK"
    if name == "mouse_move":
        x, y = args["coordinate"]
        await browser.move(int(x), int(y))
        return "OK"
    if name == "type":
        await browser.record("type", "", str(args.get("text", ""))[:60])
        await browser.type_text(str(args.get("text", "")))
        return "OK"
    if name == "key":
        combo = playwright_key(str(args.get("text", "")))
        await browser.record("key", "", combo)
        await browser.key(combo, int(args.get("repeat", 1)))
        return "OK"
    if name == "scroll":
        await browser.record("scroll", "", args.get("scroll_direction", ""))
        if args.get("coordinate"):
            await browser.move(*map(int, args["coordinate"]))
        await browser.scroll(*scroll_delta(args["scroll_direction"], args.get("scroll_amount", 3)))
        return "OK"
    if name == "wait":
        await asyncio.sleep(min(float(args.get("duration", 1)), 5))
        return "OK"
    if name == "cursor_position":
        x, y = browser.mouse
        return f"X={x},Y={y}"
    raise ActionError("지원하지 않는 동작이다: " + name)


async def _run_custom(browser: ProbeBrowser, name: str, args: dict) -> str:
    reason = str(args.get("reason", ""))
    if name == "type_marker":
        await browser.record("type_marker", reason)
        await browser.type_marker()
    elif name == "type_canary":
        await browser.record("type_canary", reason, str(args.get("canary_id", "")))
        await browser.type_canary(str(args.get("canary_id", "")))
    elif name == "type_impact":
        if not browser.impact_enabled:
            raise ActionError("이번 점검에서는 영향 시연을 하지 않는다.")
        await browser.record("type_impact", reason, browser.payload_id)
        await browser.type_impact()
    elif name == "new_session":
        await browser.record("new_session", reason)
        await browser.new_session()
    elif name == "navigate":
        await browser.record("navigate", reason, str(args.get("path", "")))
        await browser.navigate(str(args.get("path", "")))
    elif name == "finish":
        await browser.record("finish", reason, args.get("conclusion", ""))
        await browser.finish(args.get("conclusion", "could_not_reach"), reason)
        return "검사를 종료했다."
    else:
        raise ActionError("알 수 없는 도구다: " + name)
    return "OK " + json.dumps(browser.status() | {"impact_enabled": browser.impact_enabled}, ensure_ascii=False)


async def investigate(finding: Finding, target: ResolvedTarget, payload_id: str,
                      payload_template: str, run_id: str, output, emit, client=None,
                      hypothesis: dict | None = None, impact: bool = True) -> dict:
    model = model_name()
    browser = ProbeBrowser(finding, target, payload_id, payload_template, run_id, output,
                           emit, mode="computer-use")
    browser.evidence["model_name"] = model
    browser.impact_enabled = impact
    usage = {"input_tokens": 0, "output_tokens": 0}
    error = None
    try:
        if client is None:
            if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
                raise RuntimeError("공식 Computer Use 모드는 Claude API 인증(ANTHROPIC_API_KEY)이 필요합니다. "
                                   "키 없이 쓰려면 AI 에이전트(Agent SDK) 모드를 선택하세요.")
            import anthropic
            client = anthropic.AsyncAnthropic()
        await browser.start()
        await emit({"type": "state", "phase": "agent",
                    "message": "공식 Computer Use 툴셋으로 AI가 화면을 조작합니다."})
        tools = [{"type": "computer_toolset_20260801",
                  "configs": {member: {"enabled": False} for member in DISABLED_MEMBERS}},
                 *custom_tools(sorted(browser.canaries))]
        messages = [{"role": "user", "content": [
            {"type": "text", "text": agent_prompt(finding, target, hypothesis, impact)},
            *_image(await browser.screenshot()),
        ]}]
        async with asyncio.timeout(float(os.environ.get("DAST_AGENT_TIMEOUT", "300"))):
            for _turn in range(MAX_ACTIONS):
                response = await client.messages.create(
                    model=model, max_tokens=16000, system=SYSTEM_PROMPT, tools=tools,
                    output_config={"effort": "medium"}, messages=messages,
                )
                usage["input_tokens"] += getattr(response.usage, "input_tokens", 0) or 0
                usage["output_tokens"] += getattr(response.usage, "output_tokens", 0) or 0
                if response.stop_reason == "refusal":
                    raise RuntimeError("모델이 요청을 거절해 검사를 진행하지 못했습니다.")
                # 사고 블록을 포함한 응답 전체를 그대로 이어 붙인다.
                messages.append({"role": "assistant", "content": response.content})
                for block in response.content:
                    if block.type == "text" and block.text.strip():
                        await emit({"type": "agent", "action": "think", "message": block.text.strip()[:400]})
                calls = [block for block in response.content if block.type == "tool_use"]
                if not calls:
                    break
                results, halted = [], False
                for call in calls:
                    is_member = getattr(call, "toolset_name", None) == TOOLSET
                    result = {"type": "tool_result", "tool_use_id": call.id}
                    if is_member:
                        result["toolset_name"] = TOOLSET
                    if halted and is_member:
                        result.update(is_error=True, content=HALT_TEXT)
                    else:
                        try:
                            if browser.done is not None:
                                raise ActionError("검사가 이미 종료됐다.")
                            value = (await _run_member(browser, call.name, dict(call.input)) if is_member
                                     else await _run_custom(browser, call.name, dict(call.input)))
                            result["content"] = value if isinstance(value, list) else [{"type": "text", "text": value}]
                        except ActionError as exc:
                            result.update(is_error=True, content=[{"type": "text", "text": str(exc)}])
                            halted = halted or is_member
                    results.append(result)
                messages.append({"role": "user", "content": results})
                if browser.done is not None:
                    break
    except Exception as exc:
        error = exc
    finally:
        await browser.close()
    browser.evidence["usage"] = usage
    browser.evidence["cost_usd"] = round(
        usage["input_tokens"] / 1e6 * PRICE_IN + usage["output_tokens"] / 1e6 * PRICE_OUT, 6)
    return browser.verdict(error)
