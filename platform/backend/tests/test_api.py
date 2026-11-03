from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from pydantic import ValidationError
from support import LOGIN_ID, PASSWORD, settings

from delta_platform.api.app import create_app
from delta_platform.config import PASSWORD_HASHER, Settings
from delta_platform.core.auth import COOKIE, Auth
from delta_platform.core.errors import Problem
from delta_platform.core.inputs import ServiceInput
from delta_platform.core.schema import sessions

PREFIX = "/api/v1"


def test_login_protection_logout_and_rotation(client, engine):
    assert client.get(PREFIX + "/services").status_code == 401
    login = client.post(PREFIX + "/auth/login", json={"login_id": LOGIN_ID, "password": PASSWORD})
    assert login.status_code == 200
    assert (
        "HttpOnly" in login.headers["set-cookie"]
        and "SameSite=strict" in login.headers["set-cookie"]
    )
    assert "no-store" == login.headers["cache-control"]
    token = client.cookies.get(COOKIE)
    with engine.connect() as conn:
        row = conn.execute(sa.select(sessions)).mappings().one()
        assert row["token_hash"] != token and len(row["token_hash"]) == 64
    csrf = login.json()["csrf_token"]
    assert client.post(PREFIX + "/auth/logout", json={}).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    assert client.post(PREFIX + "/auth/logout", json={}).status_code == 204
    assert client.get(PREFIX + "/auth/session").status_code == 401
    client.cookies.set(COOKIE, token)
    assert client.get(PREFIX + "/services").status_code == 401


def test_session_survives_api_restart_but_not_password_rotation(signed, engine):
    token = signed.cookies.get(COOKIE)
    with TestClient(create_app(settings(), engine), base_url="http://127.0.0.1:8088") as other:
        other.cookies.set(COOKIE, token)
        assert other.get(PREFIX + "/auth/session").status_code == 200
    rotated = settings().model_copy(
        update={
            "password_hash": settings().password_hash.__class__(PASSWORD_HASHER.hash("changed"))
        }
    )
    with pytest.raises(Problem, match="다시 로그인"):
        Auth(engine, rotated).session(token)
    with engine.begin() as conn:
        conn.execute(sessions.update().values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    assert signed.get(PREFIX + "/auth/session").status_code == 401


def test_login_rate_limit_shared_between_process_instances(client, engine):
    for _ in range(5):
        assert (
            client.post(
                PREFIX + "/auth/login", json={"login_id": LOGIN_ID, "password": "wrong"}
            ).status_code
            == 401
        )
    other = Auth(engine, settings())
    with pytest.raises(Problem) as e:
        other.login(LOGIN_ID, PASSWORD, "testclient")
    assert e.value.status == 429


def test_bad_login_never_echoes_password(client):
    secret = "never-return-this"
    response = client.post(
        PREFIX + "/auth/login", json={"login_id": LOGIN_ID, "password": secret, "extra": secret}
    )
    assert response.status_code == 422 and secret not in response.text
    response = client.post(PREFIX + "/auth/login", json={"login_id": "missing", "password": secret})
    assert response.status_code == 401 and secret not in response.text


@pytest.mark.parametrize(
    "headers", [{"Origin": "https://evil.invalid"}, {"X-Platform-Request": ""}]
)
def test_login_origin_and_header_required(client, headers):
    assert (
        client.post(
            PREFIX + "/auth/login",
            headers=headers,
            json={"login_id": LOGIN_ID, "password": PASSWORD},
        ).status_code
        == 403
    )


def test_request_size_type_and_csrf(signed, data):
    assert (
        signed.post(
            PREFIX + "/services", json=data.model_dump(), headers={"X-CSRF-Token": "wrong"}
        ).status_code
        == 403
    )
    assert (
        signed.post(
            PREFIX + "/auth/login",
            content="x" * 17000,
            headers={"Content-Type": "application/json"},
        ).status_code
        == 413
    )
    assert (
        signed.post(
            PREFIX + "/auth/login", content="{}", headers={"Content-Type": "text/plain"}
        ).status_code
        == 415
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"repository_url": "http://127.0.0.1/private"},
        {"repository_url": "https://github.com:443/owner/repo"},
        {"repository_url": "https://user@github.com/owner/repo"},
        {"repository_url": "https://github.com/owner/repo?x=y"},
        {"repository_url": "https://github.com/owner/.."},
        {"dockerfile_path": "../Dockerfile"},
        {"dockerfile_path": "/Dockerfile"},
        {"dockerfile_path": "foo\\Dockerfile"},
        {"dockerfile_path": "a/" * 9 + "Dockerfile"},
        {"build_context": "app", "dockerfile_path": "Dockerfile"},
        {"healthcheck_path": "//evil.invalid/path"},
        {"container_port": 0},
        {"env": {"SECRET": "value"}},
        {"source_ref": "\nmain"},
    ],
)
def test_input_rejects_unsafe_values(data, changes):
    with pytest.raises(ValidationError):
        ServiceInput(**(data.model_dump() | changes))


def test_register_read_and_preflight_idempotency(signed, data):
    headers = {"Idempotency-Key": "registration-1"}
    first = signed.post(PREFIX + "/services", json=data.model_dump(), headers=headers)
    assert first.status_code == 201
    identifier = first.json()["id"]
    again = signed.post(PREFIX + "/services", json=data.model_dump(), headers=headers)
    assert again.status_code == 200 and again.json()["id"] == identifier
    conflict = signed.post(
        PREFIX + "/services", json=data.model_dump() | {"name": "other"}, headers=headers
    )
    assert conflict.status_code == 409
    run = signed.post(
        f"{PREFIX}/services/{identifier}/source-checks",
        json={},
        headers={"Idempotency-Key": "check-1"},
    )
    assert run.status_code == 202 and run.json()["status"] == "queued"
    replay = signed.post(
        f"{PREFIX}/services/{identifier}/source-checks",
        json={},
        headers={"Idempotency-Key": "check-1"},
    )
    assert replay.status_code == 200 and replay.json()["id"] == run.json()["id"]
    assert (
        signed.post(
            f"{PREFIX}/services/{identifier}/source-checks",
            json={},
            headers={"Idempotency-Key": "check-2"},
        ).status_code
        == 409
    )
    assert (
        signed.get(f"{PREFIX}/services/{identifier}").json()["latest_source_check"]["id"]
        == run.json()["id"]
    )
    assert (
        signed.get(f"{PREFIX}/source-checks/{run.json()['id']}/events").json()["items"][0][
            "sequence"
        ]
        == 1
    )
    assert signed.get(PREFIX + "/source-checks/" + str(uuid4())).status_code == 404
    assert signed.get(PREFIX + "/services/not-a-uuid").status_code == 422


def test_concurrent_registration_and_pagination(store, data, signed):
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: store.register(data, "same-key"), range(6)))
    assert len({r[0]["id"] for r in results}) == 1 and sum(r[1] for r in results) == 1
    for i in range(4):
        store.register(data.model_copy(update={"name": f"service-{i}"}), f"reg-{i}")
    seen = []
    cursor = None
    while True:
        page = store.list_services(2, cursor)
        seen.extend(r["id"] for r in page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 5
    assert signed.get(PREFIX + "/services?cursor=broken").status_code == 422


def test_health_and_persistence_after_new_engine(signed, engine, data):
    assert signed.get("/health/ready").status_code == 200
    item = signed.post(
        PREFIX + "/services", json=data.model_dump(), headers={"Idempotency-Key": "saved"}
    ).json()
    from delta_platform.core.database import engine_for
    from delta_platform.core.store import Store

    new_engine = engine_for(settings())
    try:
        assert str(Store(new_engine).service(item["id"])["id"]) == item["id"]
    finally:
        new_engine.dispose()


@pytest.mark.parametrize(
    "changes",
    [
        {"password_hash": "plain-text"},
        {"login_id": ""},
        {"local_mode": False, "public_origin": "http://127.0.0.1:8088"},
        {"local_mode": True, "public_origin": "http://public.example"},
        {"public_origin": "https://example.com/path"},
    ],
)
def test_bad_config_fails_closed(changes):
    values = settings().model_dump() | changes
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_production_cookie_is_secure(engine):
    config = settings().model_copy(
        update={"local_mode": False, "public_origin": "https://platform.example"}
    )
    with TestClient(create_app(config, engine), base_url="https://platform.example") as c:
        result = c.post(
            PREFIX + "/auth/login",
            json={"login_id": LOGIN_ID, "password": PASSWORD},
            headers={"Origin": "https://platform.example", "X-Platform-Request": "1"},
        )
        assert result.status_code == 200 and "Secure" in result.headers["set-cookie"]


def test_concurrent_logins_same_ip_have_valid_sessions(engine):
    auth = Auth(engine, settings())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: auth.login(LOGIN_ID, PASSWORD, "127.0.0.1"), range(2)))
    assert len({r[0] for r in results}) == 2
    for raw, _ in results:
        assert auth.session(raw)["login_id"] == LOGIN_ID


def test_missing_auth_configuration(monkeypatch):
    for name in (
        "PLATFORM_LOGIN_ID",
        "PLATFORM_PASSWORD_HASH",
        "PLATFORM_DATABASE_URL",
        "PLATFORM_PUBLIC_ORIGIN",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
