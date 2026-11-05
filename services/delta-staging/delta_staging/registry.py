"""배포 작업 관리. 클라우드마다 스테이징은 하나라서 한 번에 한 실행만 차지한다.

배포는 몇 분 걸리므로 start 는 바로 deploying 상태를 돌려주고, 진행은 get 으로 조회한다.
같은 run_id 로 다시 부르면 새로 띄우지 않는다(에이전트 재시도 대비).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from delta_staging.aws_ecs import Outcome, Report, StagingError
from delta_staging.contract import Deployment, DeployRequest, Step

logger = logging.getLogger(__name__)
ACTIVE = ("deploying", "ready")


class Provider(Protocol):
    def check_request(self, req: DeployRequest) -> None: ...
    async def deploy(self, req: DeployRequest, report: Report) -> Outcome: ...
    async def stop(self) -> None: ...
    async def endpoint(self) -> str: ...


class Unsupported(Exception):
    pass


class RequestRejected(Exception):
    pass


class Busy(Exception):
    pass


class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


class StagingRegistry:
    def __init__(self, providers: dict[str, Provider], state_path: Path | str | None = None) -> None:
        self.providers = providers
        self.state_path = Path(state_path) if state_path else None
        self._deployments: dict[str, Deployment] = self._load()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    def _load(self) -> dict[str, Deployment]:
        if not self.state_path or not self.state_path.exists():
            return {}
        loaded = [Deployment.model_validate(item) for item in json.loads(self.state_path.read_text(encoding="utf-8"))]
        # 이전 프로세스에서 진행 중이던 배포는 이어서 지켜볼 수 없다
        return {d.run_id: d.model_copy(update={"state": "failed", "ended_at": _now(), "error": "서비스 재시작으로 중단됐다"})
                if d.state == "deploying" else d for d in loaded}

    def _save(self) -> None:
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        data = [d.model_dump(mode="json") for d in self._deployments.values()]
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.state_path)

    def _put(self, deployment: Deployment) -> Deployment:
        self._deployments[deployment.run_id] = deployment
        self._save()
        return deployment

    def get(self, run_id: str) -> Deployment:
        try:
            return self._deployments[run_id]
        except KeyError as exc:
            raise NotFound(run_id) from exc

    def list(self) -> tuple[Deployment, ...]:
        return tuple(sorted(self._deployments.values(), key=lambda d: d.started_at, reverse=True))

    async def start(self, req: DeployRequest) -> Deployment:
        provider = self.providers.get(req.cloud)
        if provider is None:
            raise Unsupported(f"{req.cloud} 스테이징은 설정되지 않았다")
        async with self._lock:
            existing = self._deployments.get(req.run_id)
            if existing is not None:
                if existing.image != req.image or existing.cloud != req.cloud:
                    raise Conflict("같은 run_id 로 다른 이미지나 클라우드를 요청했다")
                return existing
            try:
                provider.check_request(req)
            except StagingError as exc:
                raise RequestRejected(str(exc)) from exc
            active = [d for d in self._deployments.values() if d.cloud == req.cloud and d.state in ACTIVE]
            for current in active:
                if not req.replace or current.state == "deploying":
                    raise Busy(f"{current.run_id} 가 스테이징을 쓰고 있다 ({current.state})")
                self._put(current.model_copy(update={"state": "replaced", "ended_at": _now(), "url": None}))
            deployment = self._put(Deployment(
                run_id=req.run_id, cloud=req.cloud, state="deploying", image=req.image, pinned=req.pinned,
                commit=req.commit, started_at=_now(),
                steps=(Step(name="accepted", at=_now(), detail="" if req.pinned else "digest 가 아닌 태그로 지정했다"),)))
            self._tasks[req.run_id] = asyncio.create_task(self._run(req, provider))
            return deployment

    def _step(self, run_id: str, name: str, detail: str = "") -> None:
        current = self._deployments[run_id]
        at = _now()
        step = Step(name=name, at=at, elapsed_seconds=round((at - current.started_at).total_seconds(), 1), detail=detail)
        self._put(current.model_copy(update={"steps": (*current.steps, step)}))

    async def _run(self, req: DeployRequest, provider: Provider) -> None:
        try:
            outcome = await provider.deploy(req, lambda name, detail: self._step(req.run_id, name, detail))
        except asyncio.CancelledError:
            raise
        except StagingError as exc:
            await self._fail(req.run_id, str(exc), provider)
            return
        except Exception as exc:  # boto 오류 등. 원문에 계정 정보가 섞일 수 있어 형식만 남긴다
            logger.exception("스테이징 배포 중 예상하지 못한 오류")
            await self._fail(req.run_id, f"예상하지 못한 오류: {type(exc).__name__}", provider)
            return
        current = self._deployments[req.run_id]
        self._put(current.model_copy(update={"state": "ready", "url": outcome.url, "revision": outcome.revision,
                                             "image_digest": outcome.image_digest, "task_arn": outcome.task_arn,
                                             "scanner_ip": outcome.scanner_ip, "ready_at": _now()}))

    async def _fail(self, run_id: str, error: str, provider: Provider) -> None:
        current = self._deployments[run_id]
        self._put(current.model_copy(update={"state": "failed", "error": error, "ended_at": _now()}))
        try:
            await provider.stop()  # 실패한 배포가 태스크를 계속 다시 띄우지 않게 내린다
        except Exception:
            logger.exception("실패한 배포를 내리지 못했다")

    async def wait(self, run_id: str) -> Deployment:
        task = self._tasks.get(run_id)
        if task is not None:
            await asyncio.shield(task)
        return self.get(run_id)

    async def stop(self, run_id: str) -> Deployment:
        current = self.get(run_id)
        if current.state not in ACTIVE:
            return current
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
        await self.providers[current.cloud].stop()
        return self._put(current.model_copy(update={"state": "stopped", "url": None, "ended_at": _now()}))


async def endpoints(providers: dict[str, Provider]) -> list[dict[str, str]]:
    """클라우드별 현재 스테이징 주소. DAST 가 점검 대상 주소를 이 값으로 받는다."""
    async def one(cloud: str, provider: Provider) -> dict[str, str]:
        try:
            return {"cloud": cloud, "url": await provider.endpoint()}
        except Exception as exc:  # 한 클라우드 조회 실패가 다른 클라우드 주소를 가리지 않게 한다
            logger.warning("스테이징 주소 조회 실패: %s", cloud, exc_info=not isinstance(exc, StagingError))
            return {"cloud": cloud, "error": str(exc) if isinstance(exc, StagingError) else type(exc).__name__}
    return list(await asyncio.gather(*(one(cloud, p) for cloud, p in sorted(providers.items()))))
