"""HTTP 진입점. uvicorn delta_staging.api:app 으로 띄운다.

환경 변수: DELTA_STAGING_TOKEN(필수), DELTA_STAGING_CONFIG(기본 staging.json), DELTA_STAGING_STATE(기본 var/deployments.json).
"""

from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from delta_staging.contract import Deployment, DeployRequest
from delta_staging.registry import Busy, Conflict, RequestRejected, NotFound, StagingRegistry, Unsupported
from delta_staging.tools import tool_catalog


def create_app(*, registry: StagingRegistry, token: str) -> FastAPI:
    if not token:
        raise ValueError("DELTA_STAGING_TOKEN 이 필요하다")
    app = FastAPI(title="Delta 스테이징 도구", version="0.1.0")
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

    @app.post("/v1/staging/deployments", status_code=202, dependencies=[Depends(require_token)])
    async def deploy(req: DeployRequest) -> Deployment:
        try:
            return await registry.start(req)
        except Unsupported as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        except RequestRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (Busy, Conflict) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/v1/staging/deployments", dependencies=[Depends(require_token)])
    async def list_deployments() -> tuple[Deployment, ...]:
        return registry.list()

    @app.get("/v1/staging/deployments/{run_id}", dependencies=[Depends(require_token)])
    async def status(run_id: str) -> Deployment:
        try:
            return registry.get(run_id)
        except NotFound as exc:
            raise HTTPException(status_code=404, detail="배포 없음") from exc

    @app.post("/v1/staging/deployments/{run_id}/stop", dependencies=[Depends(require_token)])
    async def stop(run_id: str) -> Deployment:
        try:
            return await registry.stop(run_id)
        except NotFound as exc:
            raise HTTPException(status_code=404, detail="배포 없음") from exc

    return app


def registry_from_env() -> StagingRegistry:
    from delta_staging.aws_ecs import AwsEcsStaging
    from delta_staging.config import load_targets

    targets = load_targets(os.environ.get("DELTA_STAGING_CONFIG", "staging.json"))
    providers = {cloud: AwsEcsStaging(target) for cloud, target in targets.items() if cloud == "aws"}
    return StagingRegistry(providers, os.environ.get("DELTA_STAGING_STATE", "var/deployments.json"))


def __getattr__(name: str) -> FastAPI:
    """uvicorn delta_staging.api:app 에서만 환경 변수를 읽는다. import 만 해서는 설정이 필요 없다."""
    if name == "app":
        return create_app(registry=registry_from_env(), token=os.environ.get("DELTA_STAGING_TOKEN", ""))
    raise AttributeError(name)
