import hmac
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

import httpx
import uvicorn
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, FastAPI, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from delta_platform.adapters.builds import BuildError, build_runner, build_target
from delta_platform.adapters.dast import dast_target
from delta_platform.adapters.sandbox import sandbox_target
from delta_platform.adapters.security import security_target
from delta_platform.config import Settings
from delta_platform.core.auth import COOKIE, Auth
from delta_platform.core.database import engine_for, ready
from delta_platform.core.errors import Problem
from delta_platform.core.inputs import (
    BuildInput,
    DastCheckInput,
    EmptyInput,
    ImageCheckInput,
    LoginInput,
    ServiceInput,
    request_key,
)
from delta_platform.core.store import Store

logger = logging.getLogger("delta_platform")
Limit = Annotated[int, Query(ge=1, le=100)]


def create_app(settings=None, engine=None):
    settings = settings or Settings()
    owned_engine = engine is None
    engine = engine or engine_for(settings)
    auth, store = Auth(engine, settings), Store(engine)

    @asynccontextmanager
    async def lifespan(app):
        yield
        if owned_engine:
            engine.dispose()

    app = FastAPI(
        title="Delta Platform", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.state.store, app.state.auth = store, auth

    def error_response(request, problem):
        payload = {
            "code": problem.code,
            "message": problem.message,
            "request_id": request.state.request_id,
            **problem.details,
        }
        return JSONResponse({"error": payload}, status_code=problem.status)

    @app.middleware("http")
    async def request_guard(request: Request, call_next):
        request.state.request_id = str(uuid4())
        problem = None
        if request.method == "POST":
            if (
                request.headers.get("origin") != settings.public_origin
                or request.headers.get("x-platform-request") != "1"
            ):
                problem = Problem("request_not_allowed", "허용되지 않은 요청입니다.", 403)
            elif (
                request.headers.get("content-type", "").split(";")[0].strip() != "application/json"
            ):
                problem = Problem("json_required", "JSON 요청이 필요합니다.", 415)
            else:
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 16384:
                        problem = Problem("body_too_large", "요청 크기를 초과했습니다.", 413)
                        break
                if not problem:
                    request._body = bytes(body)
        response = error_response(request, problem) if problem else await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(Problem)
    async def problem_handler(request, exc):
        return error_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        # Pydantic errors can contain the supplied password; never return raw errors.
        return error_response(
            request,
            Problem(
                "invalid_input",
                "입력값을 확인하세요.",
                422,
                fields=[".".join(str(part) for part in e["loc"]) for e in exc.errors()],
            ),
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        logger.error("database_unavailable request_id=%s", request.state.request_id)
        return error_response(
            request, Problem("database_unavailable", "저장소 연결을 확인하고 다시 시도하세요.", 503)
        )

    def authenticated(request: Request):
        summary = auth.session(request.cookies.get(COOKIE))
        if request.method == "POST" and not hmac.compare_digest(
            request.headers.get("x-csrf-token", "").encode(), summary["csrf_token"].encode()
        ):
            raise Problem("csrf_failed", "로그인 상태를 새로 확인하세요.", 403)
        return summary

    router = APIRouter(prefix="/api/v1")

    @router.post("/auth/login")
    def login(body: LoginInput, request: Request, response: Response):
        # Forwarded headers are ignored; the CLI disables proxy-header trust as well.
        token, summary = auth.login(
            body.login_id,
            body.password,
            request.client.host if request.client else "unknown",
            request.cookies.get(COOKIE),
        )
        response.set_cookie(
            COOKIE,
            token,
            httponly=True,
            secure=not settings.local_mode,
            samesite="strict",
            path="/",
            max_age=settings.session_hours * 3600,
        )
        return summary

    @router.get("/auth/session")
    def session(summary=Depends(authenticated)):
        return summary

    @router.post("/auth/logout", status_code=204, dependencies=[Depends(authenticated)])
    def logout(body: EmptyInput, request: Request):
        auth.logout(request.cookies[COOKIE])
        response = Response(status_code=204)
        response.delete_cookie(
            COOKIE, path="/", secure=not settings.local_mode, httponly=True, samesite="strict"
        )
        return response

    protected = APIRouter(dependencies=[Depends(authenticated)])

    @protected.post("/services")
    def register(body: ServiceInput, request: Request):
        result, created = store.register(body, request_key(request.headers.get("idempotency-key")))
        return JSONResponse(jsonable_encoder(result), status_code=201 if created else 200)

    @protected.get("/services")
    def service_list(limit: Limit = 50, cursor: str | None = None):
        return store.list_services(limit, cursor)

    @protected.get("/services/{service_id}")
    def service_detail(service_id: UUID):
        return store.service(service_id)

    @protected.post("/services/{service_id}/source-checks")
    def source_check(service_id: UUID, body: EmptyInput, request: Request):
        result, created = store.enqueue(
            service_id, request_key(request.headers.get("idempotency-key"))
        )
        return JSONResponse(jsonable_encoder(result), status_code=202 if created else 200)

    @protected.get("/services/{service_id}/source-checks")
    def run_list(service_id: UUID, limit: Limit = 50, cursor: str | None = None):
        return store.list_runs(service_id, limit, cursor)

    @protected.get("/source-checks/{run_id}")
    def run_detail(run_id: UUID):
        return store.run(run_id, "source_preflight")

    @protected.get("/source-checks/{run_id}/events")
    def event_list(
        run_id: UUID,
        after_sequence: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ):
        store.run(run_id, "source_preflight")
        return store.run_events(run_id, after_sequence, limit)

    @protected.post("/services/{service_id}/builds")
    def image_build(service_id: UUID, body: BuildInput, request: Request):
        result, created = store.enqueue_build(
            service_id,
            UUID(body.source_check_id),
            request_key(request.headers.get("idempotency-key")),
            build_target(settings),
        )
        return JSONResponse(jsonable_encoder(result), status_code=202 if created else 200)

    @protected.get("/services/{service_id}/builds")
    def build_list(service_id: UUID, limit: Limit = 50, cursor: str | None = None):
        return store.list_runs(service_id, limit, cursor, "image_build")

    @protected.get("/builds/{run_id}")
    def build_detail(run_id: UUID):
        return store.run(run_id, "image_build")

    @protected.get("/builds/{run_id}/events")
    def build_events(
        run_id: UUID,
        after_sequence: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ):
        store.run(run_id, "image_build")
        return store.run_events(run_id, after_sequence, limit)

    @protected.post("/services/{service_id}/security-checks")
    def security_check(service_id: UUID, body: ImageCheckInput, request: Request):
        result, created = store.enqueue_security(
            service_id,
            UUID(body.build_id),
            request_key(request.headers.get("Idempotency-Key")),
            security_target(settings),
        )
        return JSONResponse(jsonable_encoder(result), status_code=202 if created else 200)

    @protected.get("/services/{service_id}/security-checks")
    def security_list(service_id: UUID, limit: Limit = 50, cursor: str | None = None):
        return store.list_runs(service_id, limit, cursor, "security_check")

    @protected.get("/security-checks/{run_id}")
    def security_detail(run_id: UUID):
        return store.run(run_id, "security_check")

    @protected.get("/security-checks/{run_id}/events")
    def security_events(
        run_id: UUID, after_sequence: Annotated[int, Query(ge=0)] = 0, limit: Limit = 100
    ):
        store.run(run_id, "security_check")
        return store.run_events(run_id, after_sequence, limit)

    @protected.post("/services/{service_id}/dast-checks")
    def dast_check(service_id: UUID, body: DastCheckInput, request: Request):
        result, created = store.enqueue_sandbox(
            service_id,
            UUID(body.build_id),
            request_key(request.headers.get("Idempotency-Key")),
            sandbox_target(settings),
            security_check_id=UUID(body.security_check_id),
            dast_target=dast_target(settings),
        )
        return JSONResponse(jsonable_encoder(result), status_code=202 if created else 200)

    @protected.get("/services/{service_id}/dast-checks")
    def dast_list(service_id: UUID, limit: Limit = 50, cursor: str | None = None):
        return store.list_runs(service_id, limit, cursor, "dast_check")

    @protected.get("/dast-checks/{run_id}")
    def dast_detail(run_id: UUID):
        return store.run(run_id, "dast_check")

    @protected.get("/dast-checks/{run_id}/events")
    def dast_events(
        run_id: UUID, after_sequence: Annotated[int, Query(ge=0)] = 0, limit: Limit = 100
    ):
        store.run(run_id, "dast_check")
        return store.run_events(run_id, after_sequence, limit)

    @protected.post("/dast-checks/{run_id}/cancel")
    def dast_cancel(run_id: UUID, body: EmptyInput):
        return store.cancel_sandbox(run_id, "dast_check")

    @protected.post("/services/{service_id}/sandbox-checks")
    def sandbox_check(service_id: UUID, body: ImageCheckInput, request: Request):
        result, created = store.enqueue_sandbox(
            service_id,
            UUID(body.build_id),
            request_key(request.headers.get("Idempotency-Key")),
            sandbox_target(settings),
        )
        return JSONResponse(jsonable_encoder(result), status_code=202 if created else 200)

    @protected.get("/services/{service_id}/sandbox-checks")
    def sandbox_list(service_id: UUID, limit: Limit = 50, cursor: str | None = None):
        return store.list_runs(service_id, limit, cursor, "sandbox_check")

    @protected.get("/sandbox-checks/{run_id}")
    def sandbox_detail(run_id: UUID):
        return store.run(run_id, "sandbox_check")

    @protected.get("/sandbox-checks/{run_id}/events")
    def sandbox_events(
        run_id: UUID, after_sequence: Annotated[int, Query(ge=0)] = 0, limit: Limit = 100
    ):
        store.run(run_id, "sandbox_check")
        return store.run_events(run_id, after_sequence, limit)

    @protected.post("/sandbox-checks/{run_id}/cancel")
    def sandbox_cancel(run_id: UUID, body: EmptyInput):
        return store.cancel_sandbox(run_id)

    @protected.get("/builds/{run_id}/logs")
    def build_logs(run_id: UUID):
        run = store.build_execution(run_id)
        try:
            return build_runner(settings, run).log_tail(run)
        except BuildError as exc:
            raise Problem(exc.payload["code"], exc.payload["message"], 502) from None
        except (BotoCoreError, ClientError, httpx.HTTPError):
            raise Problem(
                "build_logs_unavailable",
                "빌드 로그를 가져오지 못했습니다. 잠시 후 다시 조회하세요.",
                503,
            ) from None

    router.include_router(protected)
    app.include_router(router)

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def readiness():
        try:
            ok = ready(engine)
        except SQLAlchemyError:
            ok = False
        return JSONResponse(
            {"status": "ready" if ok else "not_ready"}, status_code=200 if ok else 503
        )

    web = settings.web_dist or Path(__file__).resolve().parents[4] / "web" / "dist"
    if web.is_dir():
        app.mount("/", StaticFiles(directory=web, html=True), name="web")
    return app


def main():
    # A TLS reverse proxy may connect locally; no direct public HTTP listener.
    uvicorn.run(create_app(), host="127.0.0.1", port=8088, proxy_headers=False, access_log=False)
