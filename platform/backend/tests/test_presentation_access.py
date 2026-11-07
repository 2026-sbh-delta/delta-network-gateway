from datetime import timedelta

import sqlalchemy as sa
from fastapi.testclient import TestClient
from support import LOGIN_ID, settings

from delta_platform.api.app import create_app
from delta_platform.core.schema import sessions


def test_presentation_bootstrap_reuses_current_account_and_keeps_csrf(engine):
    config = settings().model_copy(update={"presentation_mode": True})
    with TestClient(create_app(config, engine), base_url=config.public_origin) as client:
        response = client.get("/api/v1/auth/session")
        assert response.status_code == 200
        summary = response.json()
        assert summary["login_id"] == LOGIN_ID
        assert summary["presentation_mode"] is True
        assert "HttpOnly" in response.headers["set-cookie"]
        token = client.cookies["delta_session"]
        assert client.get("/api/v1/auth/session").json()["csrf_token"] == summary["csrf_token"]
        assert client.cookies["delta_session"] == token
        assert client.get("/api/v1/services").status_code == 200
        client.headers.update({"Origin": config.public_origin, "X-Platform-Request": "1"})
        assert client.post("/api/v1/auth/logout", json={}).status_code == 403
        client.headers["X-CSRF-Token"] = summary["csrf_token"]
        assert client.post("/api/v1/auth/logout", json={}).status_code == 204
        replacement = client.get("/api/v1/auth/session")
        assert replacement.status_code == 200
        assert replacement.json()["csrf_token"] != summary["csrf_token"]


def test_presentation_replaces_expired_session(engine):
    config = settings().model_copy(update={"presentation_mode": True})
    with TestClient(create_app(config, engine), base_url=config.public_origin) as client:
        before = client.get("/api/v1/auth/session").json()
        with engine.begin() as conn:
            conn.execute(sessions.update().values(expires_at=sa.func.now() - timedelta(seconds=1)))
        after = client.get("/api/v1/auth/session").json()
        assert after["login_id"] == before["login_id"]
        assert after["csrf_token"] != before["csrf_token"]


def test_presentation_is_opt_in(client):
    assert client.get("/api/v1/auth/session").status_code == 401
    assert client.get("/api/v1/services").status_code == 401
