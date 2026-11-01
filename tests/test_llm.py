from __future__ import annotations

from types import SimpleNamespace

import pytest

from delta_ai.judge import LlmOutput
from delta_ai.llm import ClaudeLLM, LlmUnavailable


class _Messages:
    def __init__(self) -> None:
        self.kwargs = {}

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            stop_reason="end_turn", model="stub",
            content=[SimpleNamespace(type="text", text='{"items": [], "patches": []}')],
            usage=SimpleNamespace(model_dump=lambda exclude_none: {"input_tokens": 1}),
        )


async def test_claude_client_sends_schema_and_cached_system_prompt():
    messages = _Messages()
    llm = ClaudeLLM(client=SimpleNamespace(messages=messages), output=LlmOutput)
    request = SimpleNamespace(system="sys", user="usr", input_hash="h")

    response = await llm.complete(request)

    assert response.text == '{"items": [], "patches": []}'
    assert messages.kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert messages.kwargs["output_config"]["format"]["type"] == "json_schema"


def test_claude_client_without_key_is_unavailable(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(LlmUnavailable):
        ClaudeLLM(output=LlmOutput)
