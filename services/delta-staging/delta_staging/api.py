"""HTTP 진입점. uvicorn delta_staging.api:app 으로 띄운다.

권한은 둘로 나눈다.
- 조회(read): 점검 대상 주소, 배포 상태·목록, 도구 목록, 점유 조회·해제. DAST·오케스트레이터가 쓴다.
- 변경(write): 배포·중단. Jenkins 배포 단계만 쓴다. 변경 토큰은 조회도 할 수 있다.
조회 토큰으로 변경을 요청하면 403이고 배포·중단 함수는 실행되지 않는다. 토큰이 없거나 틀리면 401이다.

환경 변수: DELTA_STAGING_READ_TOKEN, DELTA_STAGING_WRITE_TOKEN(둘 다 필수, 서로 달라야 함),
DELTA_STAGING_CONFIG(기본 staging.json), DELTA_STAGING_STATE(기본 var/deployments.json).
예전 단일 토큰(DELTA_STAGING_TOKEN)은 더 이상 받지 않는다.
"""

from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from delta_staging.contract import Cloud, Deployment, DeployRequest, Lease, LeaseRelease
from delta_staging.registry import Busy, Conflict, RequestRejected, NotFound, StagingRegistry, Unsupported, endpoints
from delta_staging.tools import tool_catalog

MIN_TOKEN_LENGTH = 16


def _same(supplied: str, expected: str) -> bool:
    return hmac.compare_digest(supplied.encode(), expected.encode())


def create_app(*, registry: StagingRegistry, read_token: str, write_token: str) -> FastAPI:
    if not read_token or not write_token:
        raise ValueError("DELTA_STAGING_READ_TOKEN 과 DELTA_STAGING_WRITE_TOKEN 이 모두 필요하다")
    if len(read_token) < MIN_TOKEN_LENGTH or len(write_token) < MIN_TOKEN_LENGTH:
        raise ValueError(f"스테이징 토큰은 {MIN_TOKEN_LENGTH}자 이상이어야 한다")
    if _same(read_token, write_token):
        raise ValueError("조회 토큰과 변경 토큰이 같으면 권한을 나눌 수 없다")
    app = FastAPI(title="Delta 스테이징 도구", version="0.2.0")
    bearer = HTTPBearer(auto_error=False)

    def _supplied(credentials: HTTPAuthorizationCredentials | None) -> str:
        return credentials.credentials if credentials else ""

    def require_read(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
        supplied = _supplied(credentials)
        # 두 비교를 모두 해서 어느 토큰과 맞았는지 시간 차로 드러나지 않게 한다.
        read_ok, write_ok = _same(supplied, read_token), _same(supplied, write_token)
        if not (read_ok or write_ok):
            raise HTTPException(status_code=401, detail="인증 실패")

    def require_write(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
        supplied = _supplied(credentials)
        read_ok, write_ok = _same(supplied, read_token), _same(supplied, write_token)
        if write_ok:
            return
        if read_ok:
            raise HTTPException(status_code=403, detail="조회 권한으로는 배포·중단할 수 없다")
        raise HTTPException(status_code=401, detail="인증 실패")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/tools", dependencies=[Depends(require_read)])
    async def tools() -> dict[str, Any]:
        return tool_catalog()

    @app.post("/v1/staging/deployments", status_code=202, dependencies=[Depends(require_write)])
    async def deploy(req: DeployRequest) -> Deployment:
        try:
            return await registry.start(req)
        except Unsupported as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        except RequestRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (Busy, Conflict) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/v1/staging/targets", dependencies=[Depends(require_read)])
    async def targets() -> dict[str, Any]:
        """클라우드별 현재 스테이징 주소. 인프라를 다시 만들어 주소가 바뀌어도 여기서 새 값을 준다."""
        return {"targets": await endpoints(registry.providers)}

    @app.get("/v1/staging/deployments", dependencies=[Depends(require_read)])
    async def list_deployments() -> tuple[Deployment, ...]:
        return registry.list()

    @app.get("/v1/staging/deployments/{run_id}", dependencies=[Depends(require_read)])
    async def status(run_id: str) -> Deployment:
        try:
            return registry.get(run_id)
        except NotFound as exc:
            raise HTTPException(status_code=404, detail="배포 없음") from exc

    @app.get("/v1/staging/leases", dependencies=[Depends(require_read)])
    async def leases() -> tuple[Lease, ...]:
        return registry.leases()

    # 점유 해제는 조회 토큰으로도 된다(오케스트레이터가 점검을 끝낸 뒤 부른다). 풀어도 배포·초기화는 변경 토큰만 할 수 있다.
    @app.post("/v1/staging/leases/{cloud}/release", dependencies=[Depends(require_read)])
    async def release(cloud: Cloud, body: LeaseRelease) -> dict[str, bool]:
        try:
            return {"released": registry.release(cloud, body.holder)}
        except Conflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/v1/staging/deployments/{run_id}/stop", dependencies=[Depends(require_write)])
    async def stop(run_id: str) -> Deployment:
        try:
            return await registry.stop(run_id)
        except NotFound as exc:
            raise HTTPException(status_code=404, detail="배포 없음") from exc

    return app


def registry_from_env() -> StagingRegistry:
    from delta_staging.aws_ecs import AwsEcsStaging
    from delta_staging.config import AwsTarget, load_targets
    from delta_staging.gcp_run import GcpCloudRunStaging

    targets = load_targets(os.environ.get("DELTA_STAGING_CONFIG", "staging.json"))
    providers = {cloud: AwsEcsStaging(target) if isinstance(target, AwsTarget) else GcpCloudRunStaging(target)
                 for cloud, target in targets.items()}
    return StagingRegistry(providers, os.environ.get("DELTA_STAGING_STATE", "var/deployments.json"))


def __getattr__(name: str) -> FastAPI:
    """uvicorn delta_staging.api:app 에서만 환경 변수를 읽는다. import 만 해서는 설정이 필요 없다."""
    if name == "app":
        return create_app(registry=registry_from_env(),
                          read_token=os.environ.get("DELTA_STAGING_READ_TOKEN", ""),
                          write_token=os.environ.get("DELTA_STAGING_WRITE_TOKEN", ""))
    raise AttributeError(name)
