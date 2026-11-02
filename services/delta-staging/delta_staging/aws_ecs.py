"""AWS ECS 스테이징. 기반(클러스터·서비스·내부 ALB)은 Terraform 이 만들고, 여기서는 이미지만 바꾼다.

순서: 계정 확인 → 태스크 정의 새 개정판(이미지·env 교체) → 서비스 갱신(1개) → 배포 완료 대기 → 대상 그룹 healthy 대기.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from delta_staging.config import AwsTarget
from delta_staging.contract import DeployRequest

# describe_task_definition 결과 중 register_task_definition 에 다시 넘길 수 있는 키
_REGISTER_KEYS = ("family", "taskRoleArn", "executionRoleArn", "networkMode", "containerDefinitions", "volumes",
                  "placementConstraints", "requiresCompatibilities", "cpu", "memory", "runtimePlatform",
                  "ephemeralStorage")

Report = Callable[[str, str], None]


class StagingError(Exception):
    """배포 실패. 메시지는 에이전트와 사람에게 그대로 보여 준다."""


@dataclass(frozen=True)
class Outcome:
    url: str
    revision: str


def _session(target: AwsTarget):
    import boto3

    return boto3.session.Session(region_name=target.region)


class AwsEcsStaging:
    def __init__(self, target: AwsTarget, *, clients: dict[str, Any] | None = None, poll_seconds: float = 5,
                 rollout_timeout: float = 600, health_timeout: float = 180) -> None:
        self.target = target
        self._clients = clients
        self.poll_seconds = poll_seconds
        self.rollout_timeout = rollout_timeout
        self.health_timeout = health_timeout

    def _client(self, name: str) -> Any:
        if self._clients is None:
            session = _session(self.target)
            self._clients = {n: session.client(n) for n in ("ecs", "elbv2", "sts")}
        return self._clients[name]

    def check_image(self, image: str) -> None:
        if not any(image.startswith(prefix) for prefix in self.target.allowed_image_prefixes):
            raise StagingError(f"허용하지 않은 저장소의 이미지다. 허용: {', '.join(self.target.allowed_image_prefixes)}")

    async def _call(self, client: str, method: str, **kwargs: Any) -> dict[str, Any]:
        return await asyncio.to_thread(getattr(self._client(client), method), **kwargs)

    async def deploy(self, req: DeployRequest, report: Report) -> Outcome:
        self.check_image(req.image)
        identity = await self._call("sts", "get_caller_identity")
        if identity.get("Account") != self.target.account_id:
            raise StagingError("자격 증명의 계정이 스테이징 계정과 다르다")
        revision = await self._register(req)
        report("task_definition", revision)
        await self._call("ecs", "update_service", cluster=self.target.cluster, service=self.target.service,
                         taskDefinition=revision, desiredCount=1, forceNewDeployment=True)
        report("service_updated", f"{self.target.service} desired=1")
        await self._wait_rollout(revision)
        report("rollout_completed", revision)
        await self._wait_healthy()
        report("healthy", self.target.url)
        return Outcome(url=self.target.url, revision=revision)

    async def _register(self, req: DeployRequest) -> str:
        current = (await self._call("ecs", "describe_task_definition", taskDefinition=self.target.task_family))["taskDefinition"]
        spec = {key: current[key] for key in _REGISTER_KEYS if key in current}
        containers = []
        found = False
        for container in current["containerDefinitions"]:
            if container["name"] == self.target.container_name:
                found = True
                env = {item["name"]: item["value"] for item in container.get("environment", [])}
                env.update(req.env)
                env.update({"DELTA_RUN_ID": req.run_id, **({"DELTA_COMMIT": req.commit} if req.commit else {})})
                container = {**container, "image": req.image,
                             "environment": [{"name": k, "value": v} for k, v in sorted(env.items())]}
            containers.append(container)
        if not found:
            raise StagingError(f"태스크 정의에 컨테이너 {self.target.container_name} 이 없다")
        tags = [{"key": "DeltaRunId", "value": req.run_id}]
        registered = await self._call("ecs", "register_task_definition", **{**spec, "containerDefinitions": containers},
                                      tags=tags)
        return registered["taskDefinition"]["taskDefinitionArn"]

    async def _wait_rollout(self, revision: str) -> None:
        deadline = time.monotonic() + self.rollout_timeout
        while True:
            services = (await self._call("ecs", "describe_services", cluster=self.target.cluster,
                                         services=[self.target.service]))["services"]
            deployment = next((d for d in services[0]["deployments"] if d["taskDefinition"] == revision), None)
            if deployment is not None:
                if deployment.get("rolloutState") == "FAILED":
                    raise StagingError(f"ECS 배포 실패: {deployment.get('rolloutStateReason', '사유 없음')}")
                if deployment.get("rolloutState") == "COMPLETED" and deployment.get("runningCount", 0) >= 1:
                    return
            if time.monotonic() >= deadline:
                raise StagingError(f"ECS 배포가 {int(self.rollout_timeout)}초 안에 끝나지 않았다")
            await asyncio.sleep(self.poll_seconds)

    async def _wait_healthy(self) -> None:
        deadline = time.monotonic() + self.health_timeout
        while True:
            targets = (await self._call("elbv2", "describe_target_health",
                                        TargetGroupArn=self.target.target_group_arn))["TargetHealthDescriptions"]
            if any(t["TargetHealth"]["State"] == "healthy" for t in targets):
                return
            if time.monotonic() >= deadline:
                states = ", ".join(t["TargetHealth"]["State"] for t in targets) or "대상 없음"
                raise StagingError(f"헬스 체크가 {int(self.health_timeout)}초 안에 통과하지 않았다 ({states})")
            await asyncio.sleep(self.poll_seconds)

    async def stop(self) -> None:
        await self._call("ecs", "update_service", cluster=self.target.cluster, service=self.target.service,
                         desiredCount=0)
