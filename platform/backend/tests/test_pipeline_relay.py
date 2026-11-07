import httpx
import pytest
from fastapi.testclient import TestClient
from support import DATABASE_URL, HASH, LOGIN_ID

from delta_platform.adapters.orchestrator import OrchestratorRelay
from delta_platform.api.app import create_app
from delta_platform.config import Settings
from delta_platform.core.errors import Problem

HOST = "orch.example.cloudfront.net"
BOARD = {"run_id": "abc123", "outcome": "verified", "nodes": [{"id": "build", "state": "pass"}]}


def relay_settings(**extra):
    values = {
        "orchestrator_url": "http://orchestrator.cp.internal:8080",
        "orchestrator_public_host": HOST,
        "orchestrator_password": "orch-test-only",
    } | extra
    return Settings(
        _env_file=None,
        database_url=DATABASE_URL,
        login_id=LOGIN_ID,
        password_hash=HASH,
        presentation_mode=True,
        public_origin="http://127.0.0.1:8088",
        local_mode=True,
        **values,
    )


class FakeOrchestrator:
    def __init__(self):
        self.logins = 0
        self.session = "s1"
        self.seen = []
        self.keys = []
        self.busy = False

    def __call__(self, request):
        assert request.headers["host"] == HOST
        if request.url.path == "/login":
            assert request.content == b"password=orch-test-only"
            self.logins += 1
            cookie = f"__Host-delta_session={self.session}; Path=/; Secure; HttpOnly"
            return httpx.Response(303, headers={"set-cookie": cookie, "location": "/"})
        if request.headers.get("cookie") != f"__Host-delta_session={self.session}":
            return httpx.Response(401, json={"detail": "로그인이 필요합니다."})
        self.seen.append((request.method, request.url.path, request.content))
        if request.url.path == "/api/pipeline/runs" and request.method == "POST":
            if self.busy:
                return httpx.Response(
                    409, json={"detail": "해당 환경에 이미 실행 중인 파이프라인이 있습니다"}
                )
            self.keys.append(request.headers.get("idempotency-key"))
            return httpx.Response(202, json={"run_id": "new123"})
        if request.url.path == "/api/pipeline/runs":
            return httpx.Response(200, json={"runs": [{"run_id": "abc123"}]})
        if request.url.path == "/api/status":
            return httpx.Response(
                200,
                json={"cutover_configured": True, "release_targets": [{"id": "gcp-prod"}], "x": 1},
            )
        if request.url.path == "/api/git/refs":
            assert request.url.params["repo"].endswith("/delta-sample-app")
            return httpx.Response(
                200,
                json={
                    "repo": "r",
                    "branch": "main",
                    "branches": ["main"],
                    "commits": [{"sha": "a" * 40}],
                },
            )
        if request.url.path == "/api/pipeline/runs/abc123":
            return httpx.Response(200, json=BOARD)
        if request.url.path == "/api/pipeline/runs/abc123/live/dast":
            shot = "/api/pipeline/runs/abc123/live/dast/artifacts/finding-1.png"
            return httpx.Response(
                200, json={"status": "done", "results": [{"evidence": {"screenshots": [shot]}}]}
            )
        if request.url.path == "/api/pipeline/runs/abc123/live/dast/artifacts/finding-1.png":
            return httpx.Response(200, content=b"PNG", headers={"content-type": "image/png"})
        if request.url.path == "/api/pipeline/runs/abc123/live/dast/fix":
            return httpx.Response(200, json={"status": "ready", "patch": "--- a\n+++ b\n"})
        if request.url.path == "/api/pipeline/runs/abc123/live/dast/events":
            assert request.headers["accept"] == "text/event-stream"
            body = b'id: 1\ndata: {"type":"stage","stage":"probe"}\n\n'
            return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})
        if request.url.path == "/api/pipeline/runs/abc123/answer":
            return httpx.Response(200, json={"ok": True})
        if request.url.path == "/api/demo/reset" and request.method == "POST":
            if self.busy:
                return httpx.Response(
                    409, json={"detail": "파이프라인 실행 중에는 초기화하지 않습니다"}
                )
            return httpx.Response(202, json={"state": "running", "steps": []})
        if request.url.path == "/api/demo/reset":
            return httpx.Response(200, json={"state": "idle", "steps": []})
        return httpx.Response(404, json={"detail": "없음"})


def enter(client):
    # Presentation mode: the session call issues the shared account's cookie and CSRF token.
    client.headers.update({"Origin": "http://127.0.0.1:8088", "X-Platform-Request": "1"})
    session = client.get("/api/v1/auth/session").json()
    assert session["presentation_mode"] is True
    client.headers["X-CSRF-Token"] = session["csrf_token"]


def relay(fake):
    client = httpx.Client(transport=httpx.MockTransport(fake), follow_redirects=False)
    return OrchestratorRelay(relay_settings(), client, client)


def test_relay_logs_in_once_and_reuses_session():
    fake = FakeOrchestrator()
    r = relay(fake)
    assert r.runs() == {"runs": [{"run_id": "abc123"}]}
    assert r.run("abc123") == BOARD
    assert fake.logins == 1


def test_relay_logs_in_again_when_session_expires():
    fake = FakeOrchestrator()
    r = relay(fake)
    r.runs()
    fake.session = "s2"
    assert r.run("abc123") == BOARD
    assert fake.logins == 2


def test_relay_rejects_bad_ids_without_calling():
    fake = FakeOrchestrator()
    r = relay(fake)
    with pytest.raises(Problem) as err:
        r.run("../etc")
    assert err.value.status == 404 and fake.logins == 0


def test_relay_unconfigured_is_503():
    r = OrchestratorRelay(relay_settings(orchestrator_password=""))
    with pytest.raises(Problem) as err:
        r.runs()
    assert err.value.status == 503


def test_presentation_mode_serves_pipeline_without_login(engine):
    fake = FakeOrchestrator()
    app = create_app(relay_settings(), engine, pipeline=relay(fake))
    with TestClient(app, base_url="http://127.0.0.1:8088") as client:
        enter(client)
        assert client.get("/api/v1/pipeline/runs/abc123").json() == BOARD
        answer = client.post(
            "/api/v1/pipeline/runs/abc123/answer", json={"value": "approve", "decision_id": "d1"}
        )
        assert answer.status_code == 200
        assert (
            client.post("/api/v1/pipeline/runs/abc123/answer", json={"value": "x;rm"}).status_code
            == 422
        )
    assert fake.seen[-1][2] == b'{"value":"approve","decision_id":"d1"}'


def test_start_run_uses_fixed_repository_and_passes_reasons(engine):
    fake = FakeOrchestrator()
    app = create_app(relay_settings(), engine, pipeline=relay(fake))
    with TestClient(app, base_url="http://127.0.0.1:8088") as client:
        enter(client)
        options = client.get("/api/v1/pipeline/options").json()
        assert options["release_targets"] == [{"id": "gcp-prod"}] and "x" not in options
        body = {"branch": "main", "commit": "a" * 40, "cutover": True, "release_target": "gcp-prod"}
        started = client.post(
            "/api/v1/pipeline/runs", json=body, headers={"Idempotency-Key": "k-1"}
        )
        assert started.status_code == 202 and started.json() == {"run_id": "new123"}
        sent = fake.seen[-1][2]
        assert b'"repo":"https://github.com/2026-sbh-delta/delta-sample-app"' in sent
        assert fake.keys == ["k-1"]
        assert (
            client.post(
                "/api/v1/pipeline/runs",
                json=body | {"repo": "x"},
                headers={"Idempotency-Key": "k-2"},
            ).status_code
            == 422
        )
        fake.busy = True
        busy = client.post("/api/v1/pipeline/runs", json=body, headers={"Idempotency-Key": "k-3"})
        assert busy.status_code == 409 and "이미 실행 중" in busy.json()["error"]["message"]


def test_dast_live_view_artifacts_fix_and_events_go_through_the_relay(engine):
    fake = FakeOrchestrator()
    app = create_app(relay_settings(), engine, pipeline=relay(fake))
    with TestClient(app, base_url="http://127.0.0.1:8088") as client:
        enter(client)
        view = client.get("/api/v1/pipeline/runs/abc123/dast").json()
        shot = view["results"][0]["evidence"]["screenshots"][0]
        assert shot == "/api/v1/pipeline/runs/abc123/dast/artifacts/finding-1.png"
        image = client.get(shot)
        assert image.status_code == 200 and image.content == b"PNG"
        assert (
            client.get("/api/v1/pipeline/runs/abc123/dast/artifacts/..%2Fx.png").status_code == 404
        )
        assert client.get("/api/v1/pipeline/runs/abc123/dast/fix").json()["status"] == "ready"
        events = client.get("/api/v1/pipeline/runs/abc123/dast/events")
        assert events.headers["content-type"].startswith("text/event-stream")
        assert b'"stage":"probe"' in events.content


def test_public_https_address_needs_no_separate_host():
    r = OrchestratorRelay(
        relay_settings(
            orchestrator_url="https://d2rjxujgsi1fjg.cloudfront.net", orchestrator_public_host=""
        )
    )
    assert r.configured and r.host == "d2rjxujgsi1fjg.cloudfront.net"
    assert not OrchestratorRelay(relay_settings(orchestrator_public_host="")).configured


def test_reset_sends_the_click_as_approval_and_passes_the_busy_reason(engine):
    fake = FakeOrchestrator()
    app = create_app(relay_settings(), engine, pipeline=relay(fake))
    with TestClient(app, base_url="http://127.0.0.1:8088") as client:
        enter(client)
        assert client.get("/api/v1/pipeline/reset").json()["state"] == "idle"
        started = client.post("/api/v1/pipeline/reset", json={})
        assert started.status_code == 202 and started.json()["state"] == "running"
        assert fake.seen[-1] == ("POST", "/api/demo/reset", b'{"approve":true}')
        fake.busy = True
        busy = client.post("/api/v1/pipeline/reset", json={})
        assert busy.status_code == 409
        assert "실행 중" in busy.json()["error"]["message"]
