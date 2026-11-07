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
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from delta_platform.adapters.builds import BuildError, build_runner, build_target
from delta_platform.adapters.dast import dast_target
from delta_platform.adapters.observability import DashboardReader
from delta_platform.adapters.orchestrator import OrchestratorRelay
from delta_platform.adapters.sandbox import sandbox_target
from delta_platform.adapters.security import security_target
from delta_platform.config import Settings
from delta_platform.core.applications import ApplicationInput, ApplicationStore, database_catalog
from delta_platform.core.auth import COOKIE, Auth
from delta_platform.core.database import engine_for, ready
from delta_platform.core.deployment_targets import catalog
from delta_platform.core.deployments import DeploymentInput, DeploymentStore
from delta_platform.core.environments import EnvironmentStore
from delta_platform.core.errors import Problem
from delta_platform.core.inputs import (
    BuildInput,
    DastCheckInput,
    DeleteServiceInput,
    EmptyInput,
    ImageCheckInput,
    LoginInput,
    PipelineAnswerInput,
    PipelineStartInput,
    RemoveRuntimeInput,
    SandboxCheckInput,
    ServiceInput,
    request_key,
)
from delta_platform.core.store import Store
from delta_platform.core.workflows import ApprovalInput, WorkflowInput, WorkflowStore
from delta_platform.migration.tracking import MigrationTrackingStore

logger = logging.getLogger("delta_platform")
Limit = Annotated[int, Query(ge=1, le=100)]


def create_app(settings=None, engine=None, *, dashboard=None, pipeline=None):
    settings = settings or Settings()
    owned_engine = engine is None
    engine = engine or engine_for(settings)
    auth, store = Auth(engine, settings), Store(engine)
    environments = EnvironmentStore(engine)
    workflows = WorkflowStore(engine)
    deployments = DeploymentStore(engine, settings)
    migrations = MigrationTrackingStore(engine)
    dashboard = dashboard or DashboardReader(settings.observability_region)
    pipeline = pipeline or OrchestratorRelay(settings)

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
    def session(request: Request, response: Response):
        if not settings.presentation_mode:
            return authenticated(request)
        token, summary = auth.presentation_session(request.cookies.get(COOKIE))
        response.set_cookie(
            COOKIE,
            token,
            httponly=True,
            secure=not settings.local_mode,
            samesite="strict",
            path="/",
            max_age=settings.session_hours * 3600,
        )
        return {**summary, "presentation_mode": True}

    @router.post("/auth/logout", status_code=204, dependencies=[Depends(authenticated)])
    def logout(body: EmptyInput, request: Request):
        auth.logout(request.cookies[COOKIE])
        response = Response(status_code=204)
        response.delete_cookie(
            COOKIE, path="/", secure=not settings.local_mode, httponly=True, samesite="strict"
        )
        return response

    protected = APIRouter(dependencies=[Depends(authenticated)])

    @protected.get("/application-databases")
    def application_databases():
        return database_catalog(settings)

    @protected.post("/applications")
    def register_application(
        body: ApplicationInput, request: Request, summary=Depends(authenticated)
    ):
        result, created = ApplicationStore(engine, settings).register(
            body, request_key(request.headers.get("idempotency-key")), summary["login_id"]
        )
        return JSONResponse(jsonable_encoder(result), status_code=201 if created else 200)

    @protected.post("/applications/{service_id}/resume")
    def resume_application(service_id: UUID, body: EmptyInput):
        return ApplicationStore(engine, settings).resume(service_id)

    @protected.post("/applications/{service_id}/approve-review")
    def approve_application_review(service_id: UUID, body: EmptyInput):
        return ApplicationStore(engine, settings).resume(service_id, approve=True)

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

    @protected.post("/services/{service_id}/delete", status_code=204)
    def delete_service(service_id: UUID, body: DeleteServiceInput):
        result = store.delete_service(service_id, body.confirmation_name, settings)
        if result is None:
            return Response(status_code=204)
        return JSONResponse(jsonable_encoder(result), status_code=202)

    @protected.get("/services/{service_id}/deletion")
    def project_deletion(service_id: UUID):
        from delta_platform.core.project_removal import ProjectRemovalStore

        return ProjectRemovalStore(engine).status(service_id)

    @protected.get("/services/{service_id}/environments")
    def service_environments(service_id: UUID):
        return environments.list(service_id)

    @protected.get("/services/{service_id}/environments/{environment_id}/removal")
    def removal_preview(service_id: UUID, environment_id: UUID):
        from delta_platform.adapters.onprem_removal import configured_target
        from delta_platform.core.removals import RemovalStore

        env = environments.get(service_id, environment_id)
        try:
            target = configured_target(settings)
        except Problem:
            target = None
        return RemovalStore(engine).preview(env, target)

    @protected.post("/services/{service_id}/environments/{environment_id}/removal")
    def remove_runtime(
        service_id: UUID, environment_id: UUID, body: RemoveRuntimeInput, request: Request
    ):
        from delta_platform.adapters.onprem_removal import configured_target
        from delta_platform.core.removals import RemovalStore

        result, created = RemovalStore(engine).enqueue(
            service_id,
            environment_id,
            body.confirmation_name,
            body.binding_hash,
            request_key(request.headers.get("Idempotency-Key")),
            configured_target(settings),
        )
        return JSONResponse(jsonable_encoder(result), status_code=202 if created else 200)

    @protected.get("/services/{service_id}/observability")
    def service_observability(
        service_id: UUID,
        environment_id: UUID,
        minutes: Annotated[int, Query(ge=1, le=120)] = 15,
    ):
        result = dashboard.read(environments.get(service_id, environment_id), minutes)
        result["migration"] = migrations.latest(service_id, environment_id)
        return result

    @protected.get("/deployment-options")
    def deployment_options():
        return catalog(settings)

    @protected.post("/services/{service_id}/deployment-plans")
    def deployment_create(service_id: UUID, body: DeploymentInput, request: Request):
        result, created = deployments.create(
            service_id, body, request_key(request.headers.get("idempotency-key"))
        )
        return JSONResponse(jsonable_encoder(result), status_code=201 if created else 200)

    @protected.get("/services/{service_id}/deployment-plans")
    def deployment_list(service_id: UUID):
        return deployments.list(service_id)

    @protected.get("/deployment-plans/{plan_id}")
    def deployment_detail(plan_id: UUID):
        return deployments.get(plan_id)

    @protected.post("/deployment-plans/{plan_id}/execute")
    def deployment_execute(plan_id: UUID, body: ApprovalInput, summary=Depends(authenticated)):
        return deployments.execute(plan_id, body.input_hash, summary["login_id"])

    @protected.get("/deployment-runs/{run_id}/events")
    def deployment_events(run_id: UUID, after: Annotated[int, Query(ge=0)] = 0, limit: Limit = 50):
        store.run(run_id, "service_deploy")
        return store.run_events(run_id, after, limit)

    @protected.post("/services/{service_id}/workflows")
    def workflow_create(service_id: UUID, body: WorkflowInput, request: Request):
        result, created = workflows.create(
            service_id, body, request_key(request.headers.get("idempotency-key"))
        )
        return JSONResponse(jsonable_encoder(result), status_code=201 if created else 200)

    @protected.get("/services/{service_id}/workflows")
    def workflow_list(service_id: UUID):
        return workflows.list(service_id)

    @protected.get("/workflows/{workflow_id}")
    def workflow_detail(workflow_id: UUID):
        return workflows.get(workflow_id)

    @protected.get("/workflows/{workflow_id}/executions")
    def execution_list(workflow_id: UUID):
        return workflows.executions(workflow_id)

    @protected.post("/workflows/{workflow_id}/approval")
    def workflow_approve(workflow_id: UUID, body: ApprovalInput, summary=Depends(authenticated)):
        return workflows.approve(workflow_id, body.input_hash, summary["login_id"])

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

    @protected.get("/sandbox-targets")
    def sandbox_targets():
        items = []
        for cloud, label, runtime in (
            ("aws", "AWS", "ecs"),
            ("gcp", "GCP", "cloud_run"),
            ("onprem", "온프레미스", "k3s"),
        ):
            try:
                sandbox_target(settings, cloud)
                configured = True
            except Problem:
                configured = False
            items.append(
                {"cloud": cloud, "label": label, "runtime": runtime, "configured": configured}
            )
        return {"items": items}

    @protected.post("/services/{service_id}/dast-checks")
    def dast_check(service_id: UUID, body: DastCheckInput, request: Request):
        result, created = store.enqueue_sandbox(
            service_id,
            UUID(body.build_id),
            request_key(request.headers.get("Idempotency-Key")),
            sandbox_target(settings, body.execution_cloud),
            backend_build_id=UUID(body.backend_build_id) if body.backend_build_id else None,
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
    def sandbox_check(service_id: UUID, body: SandboxCheckInput, request: Request):
        result, created = store.enqueue_sandbox(
            service_id,
            UUID(body.build_id),
            request_key(request.headers.get("Idempotency-Key")),
            sandbox_target(settings, body.execution_cloud),
            backend_build_id=UUID(body.backend_build_id) if body.backend_build_id else None,
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

    @protected.get("/pipeline/runs")
    def pipeline_runs():
        return pipeline.runs()

    @protected.get("/pipeline/options")
    def pipeline_options(branch: Annotated[str | None, Query(max_length=100)] = None):
        return pipeline.options(settings.pipeline_repository, branch)

    @protected.post("/pipeline/runs", status_code=202)
    def pipeline_start(body: PipelineStartInput, request: Request):
        key = request_key(request.headers.get("idempotency-key"))
        return pipeline.start({"repo": settings.pipeline_repository, **body.model_dump()}, key=key)

    @protected.get("/pipeline/runs/{run_id}")
    def pipeline_run(run_id: str):
        return pipeline.run(run_id)

    @protected.get("/pipeline/runs/{run_id}/dast")
    def pipeline_dast(run_id: str):
        return pipeline.dast_view(run_id)

    @protected.get("/pipeline/runs/{run_id}/dast/fix")
    def pipeline_dast_fix(run_id: str):
        return pipeline.dast_fix(run_id)

    @protected.get("/pipeline/runs/{run_id}/dast/artifacts/{name}")
    def pipeline_dast_artifact(run_id: str, name: str):
        return Response(pipeline.dast_artifact(run_id, name), media_type="image/png")

    @protected.get("/pipeline/runs/{run_id}/dast/events")
    def pipeline_dast_events(run_id: str, request: Request):
        return StreamingResponse(
            pipeline.dast_events(run_id, request.headers.get("last-event-id")),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )

    @protected.get("/pipeline/runs/{run_id}/log/{node_id}")
    def pipeline_log(run_id: str, node_id: str):
        return pipeline.log(run_id, node_id)

    @protected.post("/pipeline/runs/{run_id}/answer")
    def pipeline_answer(run_id: str, body: PipelineAnswerInput):
        return pipeline.answer(run_id, body.value, body.decision_id, body.case_id)

    @protected.get("/pipeline/domain")
    def pipeline_domain():
        return pipeline.domain()

    # Demo reset: domain back to AWS, vulnerable build on GCP production, AI fix PRs closed.
    @protected.get("/pipeline/reset")
    def pipeline_reset_view():
        return pipeline.reset_view()

    @protected.post("/pipeline/reset", status_code=202)
    def pipeline_reset():
        return pipeline.reset()

    from delta_platform.adapters.workflow_diff import install as install_workload_diff

    install_workload_diff(protected, workflows, settings)
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
