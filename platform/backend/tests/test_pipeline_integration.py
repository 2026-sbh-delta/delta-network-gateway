"""Pipeline integration checks that do not connect to the platform database."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.staticfiles import StaticFiles

from delta_platform.adapters.pipeline_relay import COOKIE, MAX_RESPONSE_BYTES, OrchestratorRelay
from delta_platform.api import production_pipeline
from delta_platform.api.pipeline import DEFAULT_REPOSITORY, install
from delta_platform.core.errors import Problem

HOST = "pipeline.example.test"
BOARD = {"run_id": "abc123", "nodes": [{"id": "build", "state": "pass"}]}


@pytest.fixture(autouse=True)
def clean_db():
    """Override the parent database fixture for these isolated API checks."""


def settings(**extra):
    return SimpleNamespace(
        **{
            "orchestrator_url": "https://" + HOST,
            "orchestrator_password": SecretStr("server-only-test"),
            **extra,
        }
    )


class Upstream:
    def __init__(self):
        self.requests = []
        self.logins = 0
        self.session = "s1"
        self.response = None

    def __call__(self, request):
        assert request.headers["host"] == HOST
        assert request.headers["origin"] == "https://" + HOST
        if request.url.path == "/login":
            assert request.content == b"password=server-only-test"
            self.logins += 1
            return httpx.Response(
                303,
                headers={"set-cookie": f"{COOKIE}={self.session}; Path=/; Secure; HttpOnly"},
            )
        if request.headers.get("cookie") != f"{COOKIE}={self.session}":
            return httpx.Response(401)
        self.requests.append(request)
        if self.response:
            return self.response(request)
        if request.url.path == "/api/pipeline/runs":
            return httpx.Response(200, json={"runs": [{"run_id": "abc123"}]})
        return httpx.Response(200, json=BOARD)


def relay(upstream, **extra):
    return OrchestratorRelay(
        settings(**extra), httpx.Client(transport=httpx.MockTransport(upstream))
    )


def test_existing_https_settings_work_without_public_host_field():
    upstream = Upstream()
    pipeline = relay(upstream)
    assert pipeline.runs() == {"runs": [{"run_id": "abc123"}]}
    assert pipeline.run("abc123") == BOARD
    assert upstream.logins == 1


def test_internal_origin_uses_explicit_public_host_and_refreshes_expired_session():
    upstream = Upstream()
    pipeline = relay(
        upstream,
        orchestrator_url="http://orchestrator.cp.internal:8080",
        orchestrator_public_host=HOST,
    )
    pipeline.runs()
    upstream.session = "s2"
    assert pipeline.run("abc123") == BOARD
    assert upstream.logins == 2


@pytest.mark.parametrize(
    "extra",
    [
        {"orchestrator_url": "http://internal:8080"},
        {"orchestrator_url": "https://user:password@pipeline.example.test"},
        {"orchestrator_url": "https://pipeline.example.test/path"},
        {"orchestrator_public_host": "pipeline.example.test\r\nX-Test: bad"},
        {"orchestrator_password": SecretStr("")},
    ],
)
def test_invalid_settings_reject_before_upstream_login(extra):
    upstream = Upstream()
    with pytest.raises(Problem) as error:
        relay(upstream, **extra).runs()
    assert error.value.status == 503
    assert upstream.logins == 0


@pytest.mark.parametrize("run_id", ["../escape", "abc123\n", "abc?token=x"])
def test_invalid_run_identity_never_reaches_upstream(run_id):
    upstream = Upstream()
    with pytest.raises(Problem) as error:
        relay(upstream).run(run_id)
    assert error.value.status == 404
    assert upstream.logins == 0


def test_invalid_node_identity_never_reaches_upstream():
    upstream = Upstream()
    with pytest.raises(Problem):
        relay(upstream).log("abc123", "../secrets")
    assert upstream.logins == 0


@pytest.mark.parametrize(
    "response",
    [
        lambda _: httpx.Response(200, text="upstream-server-only-test"),
        lambda _: httpx.Response(200, json=["unexpected"]),
        lambda _: httpx.Response(200, text="x" * (MAX_RESPONSE_BYTES + 1)),
        lambda _: httpx.Response(500, json={"detail": "server-only-test"}),
        lambda _: httpx.Response(409, json={"detail": "server-only-test"}),
    ],
)
def test_upstream_failures_never_expose_raw_content(response):
    upstream = Upstream()
    upstream.response = response
    with pytest.raises(Problem) as error:
        relay(upstream).runs()
    assert error.value.status in (409, 502)
    assert "server-only-test" not in error.value.message


class FakePipeline:
    def __init__(self):
        self.calls = []

    def runs(self):
        self.calls.append(("runs",))
        return {"runs": [BOARD]}

    def options(self, repository, branch):
        self.calls.append(("options", repository, branch))
        return {"repository": repository, "branch": branch}

    def start(self, body, key=None):
        self.calls.append(("start", body, key))
        return {"run_id": "new123"}

    def run(self, run_id):
        self.calls.append(("run", run_id))
        return BOARD

    def log(self, run_id, node_id):
        self.calls.append(("log", run_id, node_id))
        return {"text": "test log"}

    def answer(self, *args):
        self.calls.append(("answer", *args))
        return {"ok": True}


def client_for(pipeline):
    def authenticated(request: Request):
        if request.headers.get("x-test-auth") != "session":
            raise HTTPException(401)
        if request.method == "POST" and request.headers.get("x-test-csrf") != "token":
            raise HTTPException(403)

    app = FastAPI()

    @app.exception_handler(Problem)
    def problem_handler(request, problem):
        return JSONResponse({"error": problem.code}, status_code=problem.status)

    protected = APIRouter(prefix="/api/v1", dependencies=[Depends(authenticated)])
    install(protected, settings(), relay=pipeline)
    app.include_router(protected)
    return TestClient(app)


def test_installed_routes_keep_existing_authentication_and_csrf_dependencies():
    pipeline = FakePipeline()
    with client_for(pipeline) as client:
        assert client.get("/api/v1/pipeline/runs").status_code == 401
        assert pipeline.calls == []
        client.headers["x-test-auth"] = "session"
        assert client.get("/api/v1/pipeline/runs").status_code == 200
        before = len(pipeline.calls)
        assert (
            client.post(
                "/api/v1/pipeline/runs/abc123/answer", json={"value": "approve"}
            ).status_code
            == 403
        )
        assert len(pipeline.calls) == before
        client.headers["x-test-csrf"] = "token"
        assert (
            client.post(
                "/api/v1/pipeline/runs/abc123/answer",
                json={"value": "approve", "decision_id": "d1", "case_id": "c1"},
            ).status_code
            == 200
        )
    assert pipeline.calls[-1] == ("answer", "abc123", "approve", "d1", "c1")


def test_installed_routes_work_with_legacy_settings_and_keep_repository_fixed():
    pipeline = FakePipeline()
    with client_for(pipeline) as client:
        client.headers.update({"x-test-auth": "session", "x-test-csrf": "token"})
        response = client.get("/api/v1/pipeline/options?branch=main")
        assert response.json() == {"repository": DEFAULT_REPOSITORY, "branch": "main"}
        body = {"branch": "main", "commit": "a" * 40, "release_target": "gcp-prod"}
        assert (
            client.post(
                "/api/v1/pipeline/runs", json=body, headers={"Idempotency-Key": "retry-key"}
            ).status_code
            == 202
        )
        assert pipeline.calls[-1] == (
            "start",
            {"repo": DEFAULT_REPOSITORY, **body, "cutover": False},
            "retry-key",
        )
        before = len(pipeline.calls)
        assert (
            client.post(
                "/api/v1/pipeline/runs", json={**body, "repo": "https://untrusted.test/repo"}
            ).status_code
            == 422
        )
        assert len(pipeline.calls) == before
        assert client.get("/api/v1/pipeline/runs/abc123/log/build").json() == {"text": "test log"}


def production_base(tmp_path, *, authenticated=True, partial_pipeline=False):
    def authenticate(request: Request):
        if request.cookies.get("session") != "existing-session":
            raise HTTPException(401)
        if request.method == "POST" and request.headers.get("x-csrf-token") != "existing-token":
            raise HTTPException(403)

    app = FastAPI()

    @app.middleware("http")
    async def existing_origin_guard(request, call_next):
        if request.method == "POST" and request.headers.get("origin") != "https://platform.test":
            return JSONResponse({"error": "request_not_allowed"}, status_code=403)
        return await call_next(request)

    protected = APIRouter(dependencies=[Depends(authenticate)] if authenticated else [])

    @protected.get("/services")
    def services():
        return {"items": [{"id": "preserved-service"}]}

    if partial_pipeline:

        @protected.get("/pipeline/runs")
        def existing_pipeline():
            return {"runs": []}

    api = APIRouter(prefix="/api/v1")
    api.include_router(protected)
    app.include_router(api)
    (tmp_path / "index.html").write_text("<html>Existing production UI</html>")
    app.mount("/", StaticFiles(directory=tmp_path, html=True), name="existing-web")
    return app


def test_production_wrapper_preserves_existing_routes_middleware_and_session(monkeypatch, tmp_path):
    base = production_base(tmp_path)
    original = list(base.router.routes)
    monkeypatch.setattr(
        production_pipeline, "create_base_app", lambda settings, engine, dashboard: base
    )
    pipeline = FakePipeline()
    app = production_pipeline.create_app(settings(), pipeline=pipeline)
    assert all(any(route is previous for route in app.router.routes) for previous in original)
    with TestClient(app, base_url="https://platform.test") as client:
        assert "Existing production UI" in client.get("/").text
        assert client.get("/api/v1/pipeline/runs").status_code == 401
        client.cookies.set("session", "existing-session")
        assert client.get("/api/v1/services").json() == {"items": [{"id": "preserved-service"}]}
        assert client.get("/api/v1/pipeline/runs").json() == {"runs": [BOARD]}
        url = "/api/v1/pipeline/runs/abc123/answer"
        assert client.post(url, json={"value": "approve"}).status_code == 403
        client.headers["origin"] = "https://platform.test"
        assert client.post(url, json={"value": "approve"}).status_code == 403
        client.headers["x-csrf-token"] = "existing-token"
        assert client.post(url, json={"value": "approve", "decision_id": "d1"}).status_code == 200
    assert pipeline.calls[-1] == ("answer", "abc123", "approve", "d1", None)


def test_production_wrapper_skips_complete_existing_installation(monkeypatch, tmp_path):
    base = production_base(tmp_path)
    pipeline = FakePipeline()
    router = APIRouter(prefix="/api/v1", dependencies=[Depends(lambda: None)])
    install(router, settings(), relay=pipeline)
    base.include_router(router)
    original = list(base.router.routes)
    seen = []

    def factory(settings, engine, dashboard, pipeline=None):
        seen.append(pipeline)
        return base

    monkeypatch.setattr(production_pipeline, "create_base_app", factory)
    assert production_pipeline.create_app(settings(), pipeline=pipeline) is base
    assert base.router.routes == original
    assert seen == [pipeline]


@pytest.mark.parametrize("authenticated,partial", [(False, False), (True, True)])
def test_production_wrapper_fails_closed_without_auth_or_with_partial_routes(
    monkeypatch, tmp_path, authenticated, partial
):
    base = production_base(tmp_path, authenticated=authenticated, partial_pipeline=partial)
    monkeypatch.setattr(
        production_pipeline, "create_base_app", lambda settings, engine, dashboard: base
    )
    with pytest.raises(RuntimeError):
        production_pipeline.create_app(settings(), pipeline=FakePipeline())
