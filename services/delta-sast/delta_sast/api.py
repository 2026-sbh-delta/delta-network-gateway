"""HTTP 진입점. uvicorn delta_sast.api:app 으로 띄운다.

환경 변수
- DELTA_SAST_TOKEN(필수): Bearer 토큰
- DELTA_SAST_STATE(기본 var/sast): 상태·SARIF·캐시 저장 폴더
- SAST_REPO_PREFIXES(기본 https://github.com/2026-sbh-delta/): 허용할 원격 저장소 주소 접두어(쉼표 구분)
- SAST_LOCAL_ROOTS: 허용할 로컬 경로 루트(경로 구분자 구분). 비우면 로컬 경로를 받지 않는다
- SAST_LLM_BACKEND(기본 claude-code): claude-code(로그인 사용) | api(ANTHROPIC_API_KEY) | off
- SAST_LLM_MODEL(기본 claude-sonnet-5-5)
"""

from __future__ import annotations

import hmac
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .contract import DastFeedback, FeedbackOutcome, Scan, ScanRequest
from .judge import LlmClient, client_from_env
from .registry import Conflict, NotFound, NotReady, RequestRejected, SastRegistry, Unsupported
from .source import SourcePolicy
from .tools import tool_catalog


def create_app(*, registry: SastRegistry, token: str) -> FastAPI:
    if not token:
        raise ValueError("DELTA_SAST_TOKEN이 필요하다")
    app = FastAPI(title="Delta SAST 도구", version="0.1.0")
    bearer = HTTPBearer(auto_error=False)

    def require_token(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
        supplied = credentials.credentials if credentials else ""
        if not hmac.compare_digest(supplied.encode(), token.encode()):
            raise HTTPException(status_code=401, detail="인증 실패")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/tools", dependencies=[Depends(require_token)])
    async def tools() -> dict[str, Any]:
        return tool_catalog()

    @app.post("/v1/sast/scans", status_code=202, dependencies=[Depends(require_token)])
    async def start(req: ScanRequest) -> Scan:
        try:
            return await registry.start(req)
        except Unsupported as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        except Conflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RequestRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/v1/sast/scans", dependencies=[Depends(require_token)])
    async def list_scans() -> tuple[Scan, ...]:
        return registry.list()

    @app.get("/v1/sast/scans/{run_id}", dependencies=[Depends(require_token)])
    async def status(run_id: str) -> Scan:
        try:
            return registry.get(run_id)
        except NotFound as exc:
            raise HTTPException(status_code=404, detail="점검 없음") from exc

    @app.get("/v1/sast/scans/{run_id}/sarif", dependencies=[Depends(require_token)])
    async def sarif(run_id: str) -> dict[str, Any]:
        try:
            return registry.sarif(run_id)
        except NotFound as exc:
            raise HTTPException(status_code=404, detail="점검 없음") from exc
        except NotReady as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/v1/sast/scans/{run_id}/dast-results", dependencies=[Depends(require_token)])
    async def dast_results(run_id: str, fb: DastFeedback) -> list[FeedbackOutcome]:
        try:
            return registry.feedback(run_id, fb)
        except NotFound as exc:
            raise HTTPException(status_code=404, detail="점검 없음") from exc
        except NotReady as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return app


def llm_from_env() -> Callable[[], LlmClient | None]:
    return client_from_env


def registry_from_env() -> SastRegistry:
    return SastRegistry(Path(os.environ.get("DELTA_SAST_STATE", "var/sast")), SourcePolicy.from_env(), llm_from_env())


def __getattr__(name: str) -> FastAPI:
    """uvicorn delta_sast.api:app 에서만 환경 변수를 읽는다. import만 해서는 설정이 필요 없다."""
    if name == "app":
        return create_app(registry=registry_from_env(), token=os.environ.get("DELTA_SAST_TOKEN", ""))
    raise AttributeError(name)
