"""작업실이 서버(OIDC)와 로컬(Claude 로그인)을 구분해 보여 주는지."""

import pytest

from dast_agent import server

ENV = ("DELTA_RUNTIME", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_FEDERATION_RULE_ID")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


def test_server_with_federation_is_oidc(monkeypatch):
    monkeypatch.setenv("DELTA_RUNTIME", "server")
    monkeypatch.setenv("ANTHROPIC_FEDERATION_RULE_ID", "fdrl_x")
    rt = server.llm_runtime({"logged_in": False})
    assert (rt["location"], rt["auth"]) == ("server", "oidc")


def test_local_login_is_login():
    rt = server.llm_runtime({"logged_in": True})
    assert (rt["location"], rt["auth"], rt["auth_label"]) == ("local", "login", "Claude 로그인")


def test_api_key_wins_over_federation_like_the_sdk(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_FEDERATION_RULE_ID", "fdrl_x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert server.llm_runtime({"logged_in": True})["auth"] == "api_key"


def test_nothing_configured():
    assert server.llm_runtime({"logged_in": False})["auth"] == "none"
