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
            content=[SimpleNamespace(type="text", text='{"items": []}')],
            usage=SimpleNamespace(model_dump=lambda exclude_none: {"input_tokens": 1}),
        )


async def test_claude_client_sends_schema_and_cached_system_prompt():
    messages = _Messages()
    llm = ClaudeLLM(client=SimpleNamespace(messages=messages), output=LlmOutput)
    request = SimpleNamespace(system="sys", user="usr", input_hash="h")

    response = await llm.complete(request)

    assert response.text == '{"items": []}'
    assert messages.kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert messages.kwargs["output_config"]["format"]["type"] == "json_schema"


def test_claude_client_without_key_is_unavailable(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_FEDERATION_RULE_ID"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(LlmUnavailable):
        ClaudeLLM(output=LlmOutput)


@pytest.mark.parametrize("auth_env", ["ANTHROPIC_FEDERATION_RULE_ID", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"])
def test_claude_client_accepts_platform_authentication(monkeypatch, auth_env):
    import anthropic

    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_FEDERATION_RULE_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(auth_env, "test-auth-setting")
    calls = []
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda **kw: calls.append(kw) or SimpleNamespace())

    ClaudeLLM(output=LlmOutput, client_options={"timeout": 60, "max_retries": 1})

    assert calls == [{"timeout": 60, "max_retries": 1}]


def test_claude_client_gets_explicit_timeout_and_retry_limit(monkeypatch):
    from delta_ai import api

    captured = {}

    class _Spy:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("DELTA_LLM", "claude")
    monkeypatch.setenv("DELTA_LLM_TIMEOUT_SECONDS", "12")
    monkeypatch.setattr(api, "ClaudeLLM", _Spy)

    api._llm_from_env()

    assert captured["client_options"] == {"timeout": 12.0, "max_retries": 1}
