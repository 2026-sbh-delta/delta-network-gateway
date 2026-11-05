"""공개 주소(CloudFront 뒤)에서는 로그인이 필요하고, 로컬 주소는 지금처럼 동작하는지."""

import pytest
from fastapi.testclient import TestClient

from dast_agent import server
from dast_agent.access import COOKIE, Access

PUBLIC = "https://d111.cloudfront.net"
PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def public_app(monkeypatch, tmp_path):
    monkeypatch.setenv("DAST_PUBLIC_ORIGIN", PUBLIC)
    monkeypatch.setenv("DAST_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.setenv("DAST_SESSION_SECRET", "s" * 40)
    return server.create_app(port=18765, target_port=18766, runs_dir=tmp_path)


def public_client(app):
    return TestClient(app, base_url=PUBLIC, follow_redirects=False)


def test_health_check_answers_any_host(public_app):
    with TestClient(public_app, base_url="http://10.90.10.5:8765") as client:
        response = client.get("/healthz")
    assert response.status_code == 200 and response.text == "ok"


def test_unknown_host_is_rejected(public_app):
    with TestClient(public_app, base_url="http://evil.example") as client:
        assert client.get("/api/config").status_code == 403


def test_public_requests_need_login(public_app):
    with public_client(public_app) as client:
        assert client.get("/api/config").status_code == 401
        page = client.get("/")
        assert page.status_code == 303 and page.headers["location"] == "/login"
        assert "비밀번호" in client.get("/login").text


def test_wrong_password_is_refused_and_right_one_opens_a_session(public_app):
    with public_client(public_app) as client:
        wrong = client.post("/login", data={"password": "nope"}, headers={"Origin": PUBLIC})
        assert wrong.status_code == 401 and COOKIE not in client.cookies
        ok = client.post("/login", data={"password": PASSWORD}, headers={"Origin": PUBLIC})
        assert ok.status_code == 303
        cookie = ok.headers["set-cookie"]
        assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=Strict" in cookie
        config = client.get("/api/config")
        assert config.status_code == 200 and config.json()["csrf_token"]


def test_cross_origin_login_is_refused(public_app):
    with public_client(public_app) as client:
        response = client.post("/login", data={"password": PASSWORD}, headers={"Origin": "https://evil.example"})
        assert response.status_code == 403


def test_local_address_keeps_working_without_login(public_app):
    with TestClient(public_app, base_url="http://127.0.0.1:18765") as client:
        assert client.get("/api/config").status_code == 200


def test_repeated_failures_lock_the_login():
    access = Access(PUBLIC, PASSWORD)
    for _ in range(10):
        assert not access.check_password("nope")
    assert access.locked()


def test_tampered_or_expired_session_is_invalid():
    access = Access(PUBLIC, PASSWORD, "x" * 40)
    value = access.issue()
    assert access.valid(value)
    body, _ = value.rsplit(".", 1)
    assert not access.valid(body + ".0")
    assert not access.valid("1." + value.split(".", 1)[1])


@pytest.mark.parametrize("origin, password", [("http://d111.cloudfront.net", PASSWORD),
                                              (PUBLIC + "/path", PASSWORD), (PUBLIC, "short")])
def test_public_mode_refuses_weak_setup(origin, password):
    with pytest.raises(ValueError):
        Access(origin, password)


def test_internal_host_can_only_read(monkeypatch, tmp_path):
    monkeypatch.setenv("DAST_INTERNAL_HOSTS", "dast.cp.internal:8765")
    app = server.create_app(port=18765, target_port=18766, runs_dir=tmp_path)
    with TestClient(app, base_url="http://dast.cp.internal:8765") as client:
        assert client.get("/api/runs").status_code == 200
        assert client.post("/api/sast/scans", headers={"X-DAST-Token": "x"}).status_code == 403


def test_platform_token_only_on_internal_hosts_and_platform_or_fix_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("DAST_INTERNAL_HOSTS", "dast.cp.internal:8765")
    monkeypatch.setenv("DAST_PLATFORM_TOKEN", "p" * 32)
    app = server.create_app(port=18765, target_port=18766, runs_dir=tmp_path)
    good = {"Authorization": "Bearer " + "p" * 32}
    run = "00000000-0000-4000-8000-000000000001"
    with TestClient(app, base_url="http://dast.cp.internal:8765") as client:
        # 플랫폼 API: 토큰이 맞으면 들어간다(없는 실행 = 404), 틀리거나 없으면 401
        assert client.get(f"/api/platform/runs/{run}", headers=good).status_code == 404
        assert client.get(f"/api/platform/runs/{run}", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.get(f"/api/platform/runs/{run}").status_code == 401
        # 같은 토큰으로 일반 점검 시작·관리 경로는 열리지 않는다(화면용 제어 토큰 필요)
        assert client.post("/api/runs", json={}, headers=good).status_code == 403
        assert client.post("/api/projects", json={}, headers=good).status_code == 403
    with TestClient(app, base_url="http://evil.example") as client:
        assert client.get(f"/api/platform/runs/{run}", headers=good).status_code == 403
