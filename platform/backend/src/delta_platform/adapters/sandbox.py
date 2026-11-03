"""Durable platform workflow around the existing ECS staging implementation."""

import asyncio
import json
import re
import time
from datetime import UTC, datetime

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from delta_staging.aws_ecs import AwsEcsStaging, StagingError
from delta_staging.config import AwsTarget
from delta_staging.contract import DeployRequest

from delta_platform.core.errors import LeaseLost, Problem


class SandboxError(Exception):
    def __init__(self, code, message):
        self.payload = {"code": code, "message": message}
        super().__init__(code)


def sandbox_target(settings):
    if not settings.sandbox_target:
        raise Problem("sandbox_not_configured", "샌드박스 실행기가 아직 연결되지 않았습니다.", 503)
    try:
        target = AwsTarget.model_validate(json.loads(settings.sandbox_target))
        # Only the separately provisioned platform pool is supported in this release.
        if not target.service.startswith("delta-platform-") or target.dynamic_scanner_access:
            raise ValueError()
        if target.cloud != "aws" or not target.allowed_image_prefixes:
            raise ValueError()
    except (ValueError, TypeError):
        raise Problem("sandbox_config_invalid", "샌드박스 실행기 설정을 확인하세요.", 503) from None
    return target.model_dump(mode="json")


class GuardedStaging(AwsEcsStaging):
    def __init__(self, target, guard, **kwargs):
        super().__init__(target, **kwargs)
        self.guard = guard

    async def _call(self, client, method, **kwargs):
        self.guard()
        return await super()._call(client, method, **kwargs)


class SandboxRunner:
    def __init__(self, settings, *, clients=None, poll_seconds=5, timeout=600):
        try:
            self.target = sandbox_target(settings)
        except Problem as exc:
            raise SandboxError(
                "sandbox_config_unavailable",
                "실행기 설정을 사용할 수 없습니다. 이미 시작한 ECS 태스크가 있다면 운영자가 정리를 확인하세요.",
            ) from exc
        self.clients = clients
        self.inspector = None
        self.poll_seconds, self.timeout = poll_seconds, timeout

    def execute(self, claim, store, progress):
        with store.sandbox_lock() as acquired:
            if not acquired:
                # Leave the lease for recovery; do not finish another owner's active run.
                raise LeaseLost()
            return asyncio.run(self._execute(claim, store, progress))

    async def _execute(self, claim, store, progress):
        run_id, token = claim["id"], claim["lease_token"]
        source = claim["input_snapshot"]
        if source["sandbox_target"] != self.target:
            if self.inspector:
                progress(
                    "dast_cleanup_wait",
                    "실행기 설정이 바뀌어 공간을 잠갔습니다. 기존 설정을 복원해 정리를 확인하세요.",
                )
                raise LeaseLost()
            raise SandboxError(
                "sandbox_target_changed",
                "실행기 설정이 바뀌었습니다. 기존 실행의 정리 상태를 먼저 확인하세요.",
            )
        target = AwsTarget.model_validate(self.target)
        if self.clients is None:
            config = Config(
                connect_timeout=5, read_timeout=10, retries={"mode": "standard", "max_attempts": 2}
            )
            self.clients = {
                name: boto3.client(name, region_name=target.region, config=config)
                for name in ("ecs", "elbv2", "sts", "ec2")
            }
        cleaning = False

        def guard():
            cancelled = store.sandbox_control(run_id, token)
            store.heartbeat(run_id, token)
            if cancelled and not cleaning:
                raise SandboxError("sandbox_cancelled", "샌드박스 실행 중단을 요청했습니다.")

        provider = GuardedStaging(
            target,
            guard,
            clients=self.clients,
            poll_seconds=self.poll_seconds,
            rollout_timeout=self.timeout,
            health_timeout=self.timeout,
        )
        req = DeployRequest(
            run_id=str(run_id), image=source["image"], commit=claim["resolved_commit_sha"]
        )
        revision = claim["external_id"]
        outcome = claim["submission"]

        async def service():
            response = await provider._call(
                "ecs", "describe_services", cluster=target.cluster, services=[target.service]
            )
            items = response.get("services", [])
            if len(items) != 1 or items[0].get("status") != "ACTIVE":
                raise SandboxError("sandbox_missing", "설정한 ECS 실행 공간을 찾을 수 없습니다.")
            return items[0]

        async def identity():
            value = await provider._call("sts", "get_caller_identity")
            if value.get("Account") != target.account_id:
                raise SandboxError(
                    "sandbox_account_mismatch", "샌드박스 계정과 실행 권한의 계정이 다릅니다."
                )

        async def verify_definition():
            definition = (
                await provider._call("ecs", "describe_task_definition", taskDefinition=revision)
            )["taskDefinition"]
            container = next(
                (
                    c
                    for c in definition.get("containerDefinitions", [])
                    if c["name"] == target.container_name
                ),
                {},
            )
            env = {v["name"]: v["value"] for v in container.get("environment", [])}
            if (
                definition.get("family") != target.task_family
                or container.get("image") != req.image
                or env.get("DELTA_RUN_ID") != str(run_id)
                or env.get("DELTA_COMMIT") != req.commit
                or definition.get("runtimePlatform", {}).get("cpuArchitecture") != "X86_64"
                or container.get("portMappings", [{}])[0].get("containerPort")
                != source["container_port"]
            ):
                raise SandboxError(
                    "sandbox_identity_mismatch", "ECS 개정판과 플랫폼 요청이 일치하지 않습니다."
                )

        def active(value):
            return any(value.get(k, 0) for k in ("desiredCount", "runningCount", "pendingCount"))

        try:
            guard()
            if claim["attempt"] > 3 and not outcome:
                raise SandboxError(
                    "sandbox_recovery_exhausted", "복구 횟수를 초과해 실행을 중단합니다."
                )
            if (
                not re.fullmatch(r"sha256:[a-f0-9]{64}", source["image_digest"])
                or req.image.rsplit("@", 1)[-1] != source["image_digest"]
            ):
                raise SandboxError(
                    "sandbox_image_invalid", "고정 이미지 digest가 올바르지 않습니다."
                )
            provider.check_request(req)
            await identity()
            if not outcome:
                current = await service()
                if active(current) and (not revision or current.get("taskDefinition") != revision):
                    raise SandboxError(
                        "sandbox_occupied",
                        "다른 실행이 샌드박스를 사용 중입니다. 기존 실행을 변경하지 않았습니다.",
                    )
                if not revision:
                    progress("sandbox_prepare", "빌드한 이미지의 ECS 개정판을 준비합니다.")
                    revision = await provider.register_image(req, source["container_port"])
                    store.attach_sandbox(run_id, token, revision)
                await verify_definition()
                current = await service()
                if active(current) and current.get("taskDefinition") != revision:
                    raise SandboxError(
                        "sandbox_occupied",
                        "다른 실행이 샌드박스를 사용 중입니다. 기존 실행을 변경하지 않았습니다.",
                    )
                if current.get("taskDefinition") != revision or current.get("desiredCount") != 1:
                    progress("sandbox_deploy", "격리된 ECS 공간에서 앱을 실행합니다.")
                    await provider._call(
                        "elbv2",
                        "modify_target_group",
                        TargetGroupArn=target.target_group_arn,
                        HealthCheckPath=source["healthcheck_path"],
                        HealthCheckPort="traffic-port",
                    )
                    await provider._call(
                        "ecs",
                        "update_service",
                        cluster=target.cluster,
                        service=target.service,
                        taskDefinition=revision,
                        desiredCount=1,
                        loadBalancers=[
                            {
                                "targetGroupArn": target.target_group_arn,
                                "containerName": target.container_name,
                                "containerPort": source["container_port"],
                            }
                        ],
                    )
                progress("sandbox_verify", "실제 실행 이미지와 앱의 헬스 체크를 확인합니다.")
                checked = await provider.verify_revision(revision, source["image_digest"])
                outcome = {
                    "result": {
                        "build_id": source["build_id"],
                        "image": req.image,
                        "image_digest": checked.image_digest,
                        "commit_sha": req.commit,
                        "platform": "linux/amd64",
                        "task_arn": checked.task_arn,
                        "revision": revision,
                        "runtime_check": "passed",
                        "security_check": "not_run",
                        "healthcheck_path": source["healthcheck_path"],
                        "container_port": source["container_port"],
                        "checked_at": datetime.now(UTC).isoformat(),
                    }
                }
                if self.inspector:
                    outcome["result"].update(
                        await asyncio.to_thread(
                            self.inspector.execute, claim, store, progress, guard, outcome["result"]
                        )
                    )
                    await provider.verify_revision(revision, source["image_digest"])
                outcome = store.sandbox_checkpoint(run_id, token, outcome)
        except LeaseLost:
            raise
        except (SandboxError, StagingError, BotoCoreError, ClientError) as exc:
            if isinstance(exc, SandboxError):
                error = exc.payload
            elif isinstance(exc, StagingError):
                error = {"code": "sandbox_execution_failed", "message": str(exc)[:600]}
            else:
                error = {
                    "code": "sandbox_provider_error",
                    "message": "샌드박스 실행기 연결에 실패했습니다. 권한·네트워크 설정을 확인하세요.",
                }
            outcome = store.sandbox_checkpoint(run_id, token, {"error": error})
        # Persist the terminal intent BEFORE stopping. A restarted worker only completes cleanup.
        cleaning = True
        inspection_stopped = True
        if self.inspector:
            inspection_stopped = await asyncio.to_thread(self.inspector.stop, claim)
        try:
            if revision:
                await identity()
                await verify_definition()
                current = await service()
                if active(current):
                    if current.get("taskDefinition") != revision:
                        raise SandboxError(
                            "sandbox_owner_changed", "ECS 실행 소유권이 바뀌어 중단하지 않았습니다."
                        )
                    progress("sandbox_cleanup", "실행 확인이 끝난 태스크를 정리합니다.")
                    await provider._call(
                        "ecs",
                        "update_service",
                        cluster=target.cluster,
                        service=target.service,
                        desiredCount=0,
                    )
                    deadline = time.monotonic() + 180
                    while active(await service()):
                        current = await service()
                        if (
                            current.get("taskDefinition") != revision
                            or time.monotonic() >= deadline
                        ):
                            raise SandboxError(
                                "sandbox_cleanup_timeout", "태스크 종료를 확인하지 못했습니다."
                            )
                        await asyncio.sleep(self.poll_seconds)
        except LeaseLost:
            raise
        except (SandboxError, StagingError, BotoCoreError, ClientError):
            if self.inspector:
                progress(
                    "dast_cleanup_wait",
                    "앱 종료를 확인하지 못해 공간을 잠갔습니다. 연결 복구 후 정리를 재시도합니다.",
                )
                raise LeaseLost() from None
            raise SandboxError(
                "sandbox_cleanup_required",
                "샌드박스 정리를 확인하지 못했습니다. 운영자가 이 실행의 ECS 상태를 확인해야 합니다.",
            ) from None
        if not inspection_stopped:
            progress(
                "dast_cleanup_wait",
                "앱은 종료했습니다. 검사 서버의 종료 확인 후 실행 공간을 해제합니다.",
            )
            raise LeaseLost()
        if store.sandbox_control(run_id, token):
            raise SandboxError("sandbox_cancelled", "샌드박스를 중단하고 정리했습니다.")
        if "error" in outcome:
            raise SandboxError(outcome["error"]["code"], outcome["error"]["message"])
        progress("sandbox_complete", "실행 이미지·헬스 체크를 확인하고 태스크를 종료했습니다.")
        return {
            **outcome["result"],
            "cleanup": "stopped",
            "completed_at": datetime.now(UTC).isoformat(),
        }
