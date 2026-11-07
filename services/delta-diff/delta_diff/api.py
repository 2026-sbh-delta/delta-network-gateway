"""HTTP 진입점. uvicorn delta_diff.api:app 으로 띄운다.

환경 변수: DELTA_DIFF_TOKEN(필수), DELTA_DIFF_STATE_SOURCES(필수, `버킷/접두어` 쉼표 목록), AWS_REGION,
DELTA_LLM=none|fake|claude(기본 none), DELTA_LLM_TIMEOUT_SECONDS(기본 60), DELTA_RULE_STORE(기본 var/rule_candidates.json).
DELTA_DIFF_YAML_ONLY=1이면 state 허용 목록 없이 부팅하고 모든 S3 입력을 거부한다.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
from collections.abc import Awaitable, Callable

from delta_ai.errors import TransientError
from delta_ai.fake_llm import FakeLLM
from delta_ai.judge import LlmOutput
from delta_ai.llm import ClaudeLLM, LlmClient
from delta_ai.store import RuleStore
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from delta_diff.contract import DiffRequest, DiffResult
from delta_diff.diff import MappingRejected, run_diff
from delta_diff.sources import (
    S3StateReader,
    StateAllowlist,
    StateNotAllowed,
    StateNotFound,
    StateReader,
    StateTooLarge,
    StateUnreadable,
)

MAX_BODY_BYTES = 64_000
DEFAULT_LLM_TIMEOUT_SECONDS = "60"
LLM_MAX_RETRIES = 1
logger = logging.getLogger(__name__)
STATE_ERRORS: dict[type[Exception], int] = {
    StateNotAllowed: 403,
    StateNotFound: 404,
    StateTooLarge: 413,
    MappingRejected: 422,
    StateUnreadable: 502,
}


def create_app(
    *,
    reader: StateReader,
    allowlist: StateAllowlist,
    llm: LlmClient | None,
    store: RuleStore,
    token: str,
) -> FastAPI:
    if not token:
        raise ValueError("DELTA_DIFF_TOKEN 이 필요하다")
    app = FastAPI(title="Delta 차분 에이전트", version="0.1.0")
    bearer = HTTPBearer(auto_error=False)
    lock = asyncio.Lock()

    def require_token(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),  # noqa: B008
    ) -> None:
        supplied = credentials.credentials if credentials else ""
        if not hmac.compare_digest(supplied.encode(), token.encode()):
            raise HTTPException(status_code=401, detail="인증 실패")

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"detail": "요청 형식이 잘못됐다"})

    @app.middleware("http")
    async def limit_body(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        declared = request.headers.get("content-length", "0")
        if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            return JSONResponse(status_code=413, content={"detail": "요청이 너무 크다"})
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BODY_BYTES:
                return JSONResponse(
                    status_code=413, content={"detail": "요청이 너무 크다"}
                )
        request._body = bytes(body)
        return await call_next(request)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        result = {"status": "ok", "service": "delta-diff", "api_version": "v1"}
        if not allowlist.entries:
            result.update(mode="yaml_only", state_access="unconfigured")
        return result

    @app.post("/v1/diffs", dependencies=[Depends(require_token)])
    async def create_diff(req: DiffRequest) -> DiffResult:
        try:
            async with lock:
                result = await run_diff(
                    req, reader=reader, allowlist=allowlist, llm=llm, store=store
                )
        except tuple(STATE_ERRORS) as exc:
            logger.warning("차분 실패 diff_id=%s: %s", req.diff_id, exc)
            raise HTTPException(
                status_code=STATE_ERRORS[type(exc)], detail=str(exc)
            ) from exc
        except TransientError as exc:
            logger.warning("LLM 일시 오류 diff_id=%s: %s", req.diff_id, exc)
            raise HTTPException(
                status_code=503, detail="LLM 일시 오류 — 다시 시도한다"
            ) from exc
        logger.info(
            "차분 완료 diff_id=%s source=%s target=%s 판정=%s",
            req.diff_id,
            req.source.provider,
            req.target.provider,
            result.assessment.verdict,
        )
        return result

    return app


def _llm_from_env() -> LlmClient | None:
    mode = os.environ.get("DELTA_LLM", "none")
    if mode == "none":
        return None
    if mode == "fake":
        return FakeLLM()
    if mode == "claude":
        timeout = float(
            os.environ.get("DELTA_LLM_TIMEOUT_SECONDS", DEFAULT_LLM_TIMEOUT_SECONDS)
        )
        return ClaudeLLM(
            output=LlmOutput,
            client_options={"timeout": timeout, "max_retries": LLM_MAX_RETRIES},
        )
    raise ValueError(f"DELTA_LLM 값이 잘못됐다: {mode}")


def _app_from_env() -> FastAPI:
    import boto3

    from delta_diff.sources import AssumedRoleStateReader

    role = os.environ.get("DELTA_DIFF_STATE_ROLE_ARN", "")
    external_id = os.environ.get("DELTA_DIFF_STATE_EXTERNAL_ID", "")
    sources = os.environ.get("DELTA_DIFF_STATE_SOURCES", "")
    yaml_only = os.environ.get("DELTA_DIFF_YAML_ONLY", "0")
    if yaml_only not in ("0", "1") or (yaml_only == "1" and sources.strip()):
        raise ValueError("YAML 전용 모드는 state 허용 목록과 함께 쓸 수 없다")
    allowlist = StateAllowlist(()) if yaml_only == "1" else StateAllowlist.parse(sources)
    reader = (AssumedRoleStateReader(boto3.client("sts"), role, external_id,
                                    region=os.environ.get("AWS_REGION"))
              if role else S3StateReader(boto3.client("s3")))
    return create_app(
        reader=reader,
        allowlist=allowlist,
        llm=_llm_from_env(),
        store=RuleStore(os.environ.get("DELTA_RULE_STORE", "var/rule_candidates.json")),
        token=os.environ.get("DELTA_DIFF_TOKEN", ""),
    )


def __getattr__(name: str) -> FastAPI:
    """uvicorn delta_diff.api:app 에서만 환경 변수를 읽는다."""
    if name == "app":
        return _app_from_env()
    raise AttributeError(name)
