"""점검이 끝난 항목을 두고 개발자가 AI와 대화한다.

점검 기록(SAST 위치, DAST 행동·증거·판정, AI 수정안과 코드 흐름)을 맥락으로 넣고,
대상 저장소 원본은 읽기·검색만 허용한다. 브라우저 조작·코드 수정·명령 실행은 하지 않는다.
대화 세션은 항목별로 저장해 이어서 묻을 수 있게 한다.
"""

import asyncio
import json
from pathlib import Path
import time

from .agent import model_name
from .fixer import fix_context


CHAT_PROMPT = """너는 웹 취약점 점검 결과를 개발자에게 설명하는 보안 엔지니어다.
첫 메시지의 점검 기록(JSON)이 사실의 근거다. 대상 저장소 원본은 Read·Grep·Glob으로 읽을 수 있다.

답변 원칙:
- 한국어로, 개발자가 바로 이해하도록 짧고 구체적으로 답한다.
- 왜 취약한지는 입력 → 저장·전달 → 출력 순서로 실제 코드 줄을 짚어 설명한다.
- 코드 위치는 항상 `경로:줄` 형식(예: app/comments.py:12)으로 적는다. 화면에서 그 줄로 이동하는 링크가 된다.
- 조치 방법은 이 코드베이스에 맞는 수정 코드를 짧게 보여주고, 왜 그 위치에서 막아야 하는지 말한다.
- 점검 기록에 없는 사실은 추측하지 말고 "점검 기록에 없음"이라고 말한다. 판정을 바꾸지 않는다.
- 공격용 페이로드나 우회 기법은 새로 만들어 주지 않는다. 재확인이 필요하면 재검증 기능을 안내한다.
"""

MAX_HISTORY = 60


def chat_context(record: dict, finding_id: str, fix: dict | None) -> dict:
    """대화 첫 턴에 넣을 점검 기록. 페이로드 원문과 화면 이미지는 넣지 않는다."""
    context = fix_context(record, finding_id)
    result = next((r for r in record.get("results", []) if r.get("finding_id") == finding_id), {})
    ev = result.get("evidence") or {}
    context["dast"].update({
        "verdict_reason": (ev.get("agent_conclusion") or {}).get("reason"),
        "hypothesis": ev.get("hypothesis"),
        "agent_actions": [{k: a.get(k) for k in ("action", "reason", "detail", "session")}
                          for a in ev.get("actions", [])],
        "canaries": ev.get("canaries"),
    })
    if fix and fix.get("status") == "ready":
        context["ai_fix"] = {k: fix.get(k) for k in ("summary", "changes", "verification", "trace", "patch")}
    return context


class ChatStore:
    """항목별 대화 기록과 SDK 세션 ID를 runs/<run>/chat/<finding>/chat.json에 둔다."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.file = directory / "chat.json"

    def load(self) -> dict:
        try:
            return json.loads(self.file.read_text())
        except (OSError, ValueError):
            return {"session_id": None, "messages": []}

    def save(self, data: dict):
        self.directory.mkdir(parents=True, exist_ok=True)
        data["messages"] = data["messages"][-MAX_HISTORY:]
        temp = self.file.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        temp.replace(self.file)


async def ask(store: ChatStore, question: str, context: dict, repo_root: Path):
    """질문 하나에 답한다. {"type": "text"|"tool"|"done"|"error", ...} 이벤트를 차례로 낸다."""
    from claude_agent_sdk import (
        AssistantMessage, ClaudeAgentOptions, PermissionResultAllow, PermissionResultDeny,
        ResultMessage, StreamEvent, ToolUseBlock, query,
    )

    root = repo_root.resolve()

    async def permission(tool_name, tool_input, _context):
        # 대상 저장소 원본의 읽기·검색만 허용한다.
        if tool_name not in {"Read", "Grep", "Glob"}:
            return PermissionResultDeny(message="읽기 전용 대화입니다.")
        target = tool_input.get("file_path") or tool_input.get("path")
        if target:
            path = (root / target).resolve() if not Path(target).is_absolute() else Path(target).resolve()
            if root not in [path, *path.parents]:
                return PermissionResultDeny(message="대상 저장소 밖의 파일입니다.")
        return PermissionResultAllow()

    data = store.load()
    data["messages"].append({"role": "user", "text": question, "at": time.time()})
    store.directory.mkdir(parents=True, exist_ok=True)
    extra = {"resume": data["session_id"]} if data.get("session_id") else {}
    prompt = question if extra else (
        "점검 기록(JSON):\n" + json.dumps(context, ensure_ascii=False)
        + f"\n\n대상 저장소 원본 경로: {root}\n\n개발자 질문: {question}")
    options = ClaudeAgentOptions(
        model=model_name(), tools=["Read", "Grep", "Glob"], allowed_tools=[],
        setting_sources=[], skills=[], plugins=[], mcp_servers={}, strict_mcp_config=True,
        can_use_tool=permission, cwd=str(store.directory), max_turns=12, max_budget_usd=0.5,
        include_partial_messages=True, effort="low", add_dirs=[str(root)],
        stderr=lambda _line: None, system_prompt=CHAT_PROMPT, **extra,
    )
    answer, streamed, result = [], False, None
    try:
        async with asyncio.timeout(150):
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, StreamEvent):
                    event = message.event
                    if event.get("type") == "content_block_delta" and event["delta"].get("type") == "text_delta":
                        streamed = True
                        answer.append(event["delta"]["text"])
                        yield {"type": "text", "text": event["delta"]["text"]}
                    elif event.get("type") == "message_start" and answer and not answer[-1].endswith("\n"):
                        # 도구를 쓴 뒤 이어지는 답은 문단을 나눈다.
                        answer.append("\n\n")
                        yield {"type": "text", "text": "\n\n"}
                elif isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, ToolUseBlock):
                            target = block.input.get("file_path") or block.input.get("pattern") or ""
                            yield {"type": "tool", "name": block.name,
                                   "target": str(target).replace(str(root) + "/", "")[:160]}
                elif isinstance(message, ResultMessage):
                    result = message
    except Exception as exc:
        text = "".join(answer).strip()
        if text:
            data["messages"].append({"role": "assistant", "text": text, "at": time.time(), "partial": True})
        store.save(data)
        yield {"type": "error", "message": str(exc) if type(exc) is RuntimeError else type(exc).__name__}
        return
    text = "".join(answer).strip()
    if not streamed and result is not None and getattr(result, "result", None):
        text = result.result.strip()
        yield {"type": "text", "text": text}
    if result is not None and getattr(result, "session_id", None):
        data["session_id"] = result.session_id
    data["messages"].append({"role": "assistant", "text": text or "(답변 없음)", "at": time.time(),
                             "cost_usd": getattr(result, "total_cost_usd", None)})
    store.save(data)
    yield {"type": "done", "session_id": data.get("session_id")}
