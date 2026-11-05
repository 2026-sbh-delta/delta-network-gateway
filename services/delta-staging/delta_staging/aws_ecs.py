"""AWS ECS 스테이징. 기반(클러스터·서비스·내부 ALB)은 Terraform 이 만들고, 여기서는 이미지만 바꾼다.

순서: 계정 확인 → 태스크 정의 새 개정판(이미지·env 교체) → 서비스 갱신(1개) → 새 태스크 실행·digest 확인
→ 대상 그룹에 새 태스크만 healthy 로 남을 때 ready.
ECS 의 배포 완료 표시는 헬스 체크 통과 뒤에도 수십 초 늦게 붙어서 기다리지 않는다.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import time
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from delta_staging.config import AwsTarget
from delta_staging.contract import DeployRequest, bundle_hash

# describe_task_definition 결과 중 register_task_definition 에 다시 넘길 수 있는 키
_REGISTER_KEYS = ("family", "taskRoleArn", "executionRoleArn", "networkMode", "containerDefinitions", "volumes",
                  "placementConstraints", "requiresCompatibilities", "cpu", "memory", "runtimePlatform",
                  "ephemeralStorage")

Report = Callable[[str, str], None]
SCANNER_TAG = {"Key": "DeltaManaged", "Value": "scanner"}
CHECKIP_URL = "https://checkip.amazonaws.com"
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
S06_HANDOFF = ("previous_dast_shutdown", "write_drain", "pre_dast_zero_rows")


def cloud_failure(message: object) -> str:
    # 이전 호출자의 진단 호환 문구만 허용한다. 임의 API 메시지·SQL·비밀은 노출하지 않는다.
    safe = {"tasks failed to start", "container failed to start"}
    return message if isinstance(message, str) and message in safe else "클라우드 오류 상세 원문 생략"


class StagingError(Exception):
    """배포 실패. 메시지는 에이전트와 사람에게 그대로 보여 준다."""


@dataclass(frozen=True)
class Outcome:
    url: str
    revision: str
    image_digest: str
    task_arn: str
    scanner_ip: str | None = None
    images: dict[str, str] = field(default_factory=dict)
    image_digests: dict[str, str] = field(default_factory=dict)
    bundle_sha256: str | None = None
    data_reset_remaining: int | None = None
    databaseVersionNum: int | None = None
    database_version_num: int | None = None
    s06_verified: bool = False
    pre_dast_remaining: int | None = None


@dataclass(frozen=True)
class ScanSafety:
    """호출자가 실제 DAST 종료·drain·DB 조회에서 수집해 전달하는 회차별 관측. 요청 bool이 아니다."""

    run_id: str
    lease_holder: str
    cloud: str
    database_name: str
    previous_dast_complete: bool
    writes_drained: bool
    revision: str | None = None
    remaining_rows: int | None = None


SafetyCheck = Callable[[DeployRequest, str | None], Awaitable[ScanSafety]]


def health_safety(endpoint: Callable[[], Awaitable[str]], database_name: str,
                  probe: Callable[[str], dict[str, Any]] | None = None) -> SafetyCheck:
    """S06 기본 관측. 이전 DAST 종료는 스테이징 점유가 보장한다(점유를 얻었으면 이전 실행은 끝났거나 만료됐다).
    쓰기 drain·남은 행 수는 BE 헬스(activeWrites·remainingRows)로 본다. 교체 전 앱이 없거나 예전 앱이면 쓰기도 없다."""

    async def check(req: DeployRequest, revision: str | None) -> ScanSafety:
        url = await endpoint()
        value = await asyncio.to_thread(probe or health_json, url)
        # 새 개정판 확인은 관측값이 있어야 통과한다. 헬스 한 번이 비면(일시 오류) 짧게 다시 묻는다.
        for _ in range(5 if revision is not None else 0):
            if "activeWrites" in value and "remainingRows" in value:
                break
            await asyncio.sleep(1)
            value = await asyncio.to_thread(probe or health_json, url)
        writes, rows = value.get("activeWrites"), value.get("remainingRows")
        drained = writes in (None, 0) if revision is None else writes == 0
        return ScanSafety(run_id=req.run_id, lease_holder=req.lease_holder or req.run_id, cloud=req.cloud,
                          database_name=database_name, previous_dast_complete=True, writes_drained=drained,
                          revision=revision, remaining_rows=rows if type(rows) is int else None)

    return check


async def verify_scan_safety(check: SafetyCheck | None, req: DeployRequest, database_name: str,
                             revision: str | None = None) -> ScanSafety:
    if check is None:
        raise StagingError("S06 관측 훅이 연결되지 않았다. 이전 DAST 종료·drain·검사 직전 행0 확인 필요")
    try:
        observed = await check(req, revision)
    except Exception:
        raise StagingError("S06 관측 조회 실패. 원문은 비밀 보호를 위해 생략했다") from None
    if (not isinstance(observed, ScanSafety) or observed.run_id != req.run_id
            or observed.lease_holder != (req.lease_holder or req.run_id) or observed.cloud != req.cloud
            or observed.database_name != database_name
            or observed.previous_dast_complete is not True or observed.writes_drained is not True):
        raise StagingError("S06의 이전 DAST 종료·쓰기 drain 관측이 없거나 다른 회차의 증거다")
    if revision is not None and (observed.revision != revision or type(observed.remaining_rows) is not int
                                 or observed.remaining_rows != 0):
        raise StagingError("S06의 새 revision·DAST 직전 정수형 행0 관측이 없다")
    return observed


def reset_result(messages: list[str]) -> dict[str, int] | None:
    """Node reset stdout의 단일 구조화 결과만 수용한다. 로그 원문은 오류에도 포함하지 않는다."""
    results = []
    for message in messages:
        try:
            value = json.loads(message)
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and ("remaining_rows" in value or "database_version_num" in value):
            results.append(value)
    if not results:
        return None
    if len(results) != 1:
        raise StagingError("초기화 구조화 출력이 중복됐다")
    result = results[0]
    remaining, version = result.get("remaining_rows"), result.get("database_version_num")
    if type(remaining) is not int or remaining != 0 or type(version) is not int or version // 10000 != 18:
        raise StagingError("초기화 잔존 행 또는 PostgreSQL18 관측이 유효하지 않다")
    return {"remaining_rows": remaining, "database_version_num": version}


def health_json(url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/health", timeout=5) as response:
            value = json.loads(response.read(65536))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def check_health(value: dict[str, Any], req: DeployRequest) -> int:
    version = value.get("databaseVersionNum")
    if (value.get("status") != "ok" or value.get("storage") != "postgres"
            or value.get("environment") != "staging" or value.get("version") != req.commit
            or value.get("cloudProvider") != req.cloud
            or type(version) is not int or version // 10000 != 18):
        raise StagingError("FE→BE→DB 상태·커밋·클라우드·PostgreSQL18 관측이 일치하지 않는다")
    return version


def current_public_ip() -> str:
    """도구를 실행하는 곳의 현재 공인 IP. 실행 환경이 바뀌어도 매번 확인한다."""
    with urllib.request.urlopen(CHECKIP_URL, timeout=5) as response:  # noqa: S310 고정 주소
        value = response.read().decode().strip()
    address = ipaddress.ip_address(value)
    if address.version != 4 or not address.is_global:
        raise StagingError(f"공인 IPv4 를 확인하지 못했다: {value}")
    return value


def _session(target: AwsTarget):
    import boto3

    return boto3.session.Session(region_name=target.region)


class AwsEcsStaging:
    def __init__(self, target: AwsTarget, *, clients: dict[str, Any] | None = None, poll_seconds: float = 5,
                 rollout_timeout: float = 600, health_timeout: float = 180,
                 reset_timeout: float = 240, health_probe: Callable[[str], dict[str, Any]] = health_json,
                 safety_check: SafetyCheck | None = None,
                 public_ip: Callable[[], str] = current_public_ip) -> None:
        self.target = target
        self.public_ip = public_ip
        self._clients = clients
        self.poll_seconds = poll_seconds
        self.rollout_timeout = rollout_timeout
        self.health_timeout = health_timeout
        self.reset_timeout = reset_timeout
        self.health_probe = health_probe
        self.safety_check = safety_check
        self._schema_prepared = target.schema_prepared

    def _client(self, name: str) -> Any:
        if self._clients is None:
            session = _session(self.target)
            self._clients = {n: session.client(n) for n in ("ecs", "elbv2", "sts", "ec2", "logs")}
        return self._clients[name]

    def check_request(self, req: DeployRequest) -> None:
        self.check_image(req.image)
        for image in (req.images or {}).values():
            self.check_image(image)
        if req.images and (self.target.environment != "staging" or not self.target.backend_container_name
                           or not self.target.reset_task_family or not self.target.database_name):
            raise StagingError("v2에는 staging의 BE·reset task·database_name 설정이 필요하다")
        if req.images and self.safety_check is None:
            raise StagingError("v2에는 S06 관측 훅 연결이 필요하다. 라이브러리 호출자가 제공해야 한다")
        if req.scanner_ip and not self.target.dynamic_scanner_access:
            raise StagingError("이 스테이징은 내부 전용이라 scanner_ip 를 받지 않는다")

    def check_image(self, image: str) -> None:
        if not any(image.startswith(prefix) for prefix in self.target.allowed_image_prefixes):
            raise StagingError(f"허용하지 않은 저장소의 이미지다. 허용: {', '.join(self.target.allowed_image_prefixes)}")

    async def _call(self, client: str, method: str, **kwargs: Any) -> dict[str, Any]:
        return await asyncio.to_thread(getattr(self._client(client), method), **kwargs)

    async def endpoint(self) -> str:
        """스테이징 주소. 설정에 없으면 대상 그룹이 붙은 ALB 의 DNS 이름을 조회한다."""
        if self.target.url:
            return self.target.url.rstrip("/")
        groups = (await self._call("elbv2", "describe_target_groups",
                                   TargetGroupArns=[self.target.target_group_arn]))["TargetGroups"]
        arns = groups[0].get("LoadBalancerArns") if groups else None
        if not arns:
            raise StagingError("대상 그룹에 붙은 ALB 가 없다")
        balancers = (await self._call("elbv2", "describe_load_balancers", LoadBalancerArns=arns[:1]))["LoadBalancers"]
        return f"http://{balancers[0]['DNSName']}"

    async def deploy(self, req: DeployRequest, report: Report) -> Outcome:
        self.check_request(req)
        if req.images:
            await verify_scan_safety(self.safety_check, req, self.target.database_name)
            report("scan_safety", "이전 DAST 종료·교체 전 쓰기 drain 확인")
        identity = await self._call("sts", "get_caller_identity")
        if identity.get("Account") != self.target.account_id:
            raise StagingError("자격 증명의 계정이 스테이징 계정과 다르다")
        scanner_ip = None
        if req.scanner_ip:
            scanner_ip = await asyncio.to_thread(self.public_ip) if req.scanner_ip == "auto" else req.scanner_ip
            await self._allow_scanner(scanner_ip, req.run_id)
            report("scanner_allowed", f"{scanner_ip}/32")
        if req.images:
            return await self._deploy_bundle(req, report, scanner_ip)
        revision = await self._register(req)
        report("task_definition", revision)
        await self._call("ecs", "update_service", cluster=self.target.cluster, service=self.target.service,
                         taskDefinition=revision, desiredCount=1, forceNewDeployment=True)
        report("service_updated", f"{self.target.service} desired=1")
        task_arn, digest, ip = await self._new_task(revision)
        if "@sha256:" in req.image and req.image.rsplit("@", 1)[1] != digest:
            raise StagingError(f"실행 중인 이미지 digest가 요청과 다르다: {digest}")
        report("image_digest", digest)
        await self._wait_healthy(ip)
        url = await self.endpoint()
        report("healthy", url)
        return Outcome(url=url, revision=revision, image_digest=digest, task_arn=task_arn,
                       scanner_ip=scanner_ip)

    async def _deploy_bundle(self, req: DeployRequest, report: Report, scanner_ip: str | None) -> Outcome:
        services = (await self._call("ecs", "describe_services", cluster=self.target.cluster,
                                     services=[self.target.service])).get("services", [])
        bindings = services[0].get("loadBalancers", []) if len(services) == 1 else []
        if (not any(b.get("targetGroupArn") == self.target.target_group_arn
                    and b.get("containerName") == self.target.container_name and b.get("containerPort") == 3000
                    for b in bindings)
                or any(b.get("containerName") == self.target.backend_container_name for b in bindings)):
            raise StagingError("ALB는 FE3000에만 연결돼야 한다. staging 인프라 설정 확인 필요")
        revision = await self._register(req)
        report("task_definition", revision)
        reset = None
        if not self._schema_prepared:
            result = await self._reset_bundle(req)
            self._schema_prepared = True
            report("schema_prepared", "최초 스키마 준비와 초기화 관측 완료")
        else:
            reset = asyncio.create_task(self._reset_bundle(req))
        try:
            await self._call("ecs", "update_service", cluster=self.target.cluster, service=self.target.service,
                             taskDefinition=revision, desiredCount=1, forceNewDeployment=True)
            report("service_updated", self.target.service)
            task_arn, _, ip = await self._new_task(revision, req)
            tasks = (await self._call("ecs", "describe_tasks", cluster=self.target.cluster, tasks=[task_arn])).get("tasks", [])
            task = next((t for t in tasks if t.get("taskArn") == task_arn
                         and t.get("taskDefinitionArn") == revision and t.get("lastStatus") == "RUNNING"), None)
            if task is None:
                raise StagingError("검증 대상 태스크가 실행 중인 새 개정판이 아니다")
            observed = {}
            images = {}
            for role, name in {"frontend": self.target.container_name, "backend": self.target.backend_container_name}.items():
                containers = [c for c in task.get("containers", []) if c.get("name") == name]
                if len(containers) != 1 or containers[0].get("lastStatus") != "RUNNING":
                    raise StagingError("FE·BE 두 컨테이너가 실행 중이어야 한다")
                digest = containers[0].get("imageDigest", "")
                if digest != req.image_digests[role] or containers[0].get("image") != req.images[role]:
                    raise StagingError("실행 중인 FE·BE imageDigest가 요청과 다르다")
                observed[role] = digest
                images[role] = containers[0]["image"]
            report("image_digest", json.dumps(observed, sort_keys=True))
            await self._wait_healthy(ip)
            if reset is not None:
                result = await reset
            report("data_reset", f"{self.target.reset_task_family}: 남은 행 0")
            url = await self.endpoint()
            version = await self._wait_bundle_health(req, url)
            await self._wait_healthy(ip)
            safety = await verify_scan_safety(self.safety_check, req, self.target.database_name, revision)
            report("healthy", "FE→BE→PostgreSQL18 확인")
            report("pre_dast_zero", "새 task 전환 후 DAST 직전 행0 관측 확인")
            return Outcome(url, revision, observed["frontend"], task_arn, scanner_ip,
                           images=images, image_digests=observed, bundle_sha256=bundle_hash(req.commit, observed),
                           data_reset_remaining=result["remaining_rows"], databaseVersionNum=version,
                           database_version_num=version, s06_verified=True, pre_dast_remaining=safety.remaining_rows)
        finally:
            if reset is not None:
                if not reset.done():
                    reset.cancel()
                await asyncio.gather(reset, return_exceptions=True)

    async def _wait_bundle_health(self, req: DeployRequest, url: str) -> int:
        deadline = time.monotonic() + self.health_timeout
        while True:
            try:
                return check_health(await asyncio.to_thread(self.health_probe, url), req)
            except StagingError:
                if time.monotonic() >= deadline:
                    raise
            await asyncio.sleep(self.poll_seconds)

    async def register_image(self, req: DeployRequest, container_port: int) -> str:
        """Prepare a revision without starting it; durable callers persist it before activation."""
        self.check_request(req)
        if not 1 <= container_port <= 65535:
            raise StagingError("앱 포트가 올바르지 않다")
        return await self._register(req, container_port=container_port)

    async def verify_revision(self, revision: str, expected_digest: str) -> Outcome:
        """Observe the exact revision, including digest and exclusive target health."""
        task_arn, digest, ip = await self._new_task(revision)
        if digest != expected_digest:
            raise StagingError("실행 중인 이미지 digest가 요청과 다르다")
        await self._wait_healthy(ip)
        return Outcome(await self.endpoint(), revision, digest, task_arn)

    async def _register(self, req: DeployRequest, container_port: int | None = None) -> str:
        current = (await self._call("ecs", "describe_task_definition", taskDefinition=self.target.task_family))["taskDefinition"]
        spec = {key: current[key] for key in _REGISTER_KEYS if key in current}
        containers = []
        found = False
        found_backend = False
        for container in current["containerDefinitions"]:
            frontend = container["name"] == self.target.container_name
            backend = bool(req.images and container["name"] == self.target.backend_container_name)
            if frontend or backend:
                found = found or frontend
                found_backend = found_backend or backend
                env = {item["name"]: item["value"] for item in container.get("environment", [])}
                if backend and env.get("PGDATABASE") != self.target.database_name:
                    raise StagingError("BE task 정의의 DB 이름이 staging target과 다르다")
                env.update(req.env)
                env.update({"DELTA_RUN_ID": req.run_id, **({"DELTA_COMMIT": req.commit} if req.commit else {})})
                if req.images:
                    env.update({"APP_ENV": "staging", "APP_VERSION": req.commit, "CLOUD_PROVIDER": "aws",
                                "PORT": "3000" if frontend else "8080",
                                "IMAGE_DIGEST": req.image_digests["frontend" if frontend else "backend"]})
                    env.update({"API_HOST": "127.0.0.1", "API_PORT": "8080"} if frontend else {"STORAGE_BACKEND": "postgres"})
                if container_port is not None:
                    container = {**container, "portMappings": [{"containerPort": container_port, "protocol": "tcp"}]}
                image = req.images["frontend" if frontend else "backend"] if req.images else req.image
                container = {**container, "image": image,
                             "environment": [{"name": k, "value": v} for k, v in sorted(env.items())]}
                if req.images:
                    container = {**container, "essential": True,
                                 "portMappings": [{"containerPort": 3000 if frontend else 8080, "protocol": "tcp"}]}
            containers.append(container)
        if not found:
            raise StagingError(f"태스크 정의에 컨테이너 {self.target.container_name} 이 없다")
        if req.images and not found_backend:
            raise StagingError("태스크 정의에 BE 컨테이너가 없다. 인프라 변경이 필요하다")
        tags = [{"key": "DeltaRunId", "value": req.run_id}]
        registered = await self._call("ecs", "register_task_definition", **{**spec, "containerDefinitions": containers},
                                      tags=tags)
        return registered["taskDefinition"]["taskDefinitionArn"]

    async def _reset_bundle(self, req: DeployRequest) -> dict[str, int]:
        """앱 서비스와 분리된 reset 자격의 한 태스크를 실행하고 exit·digest·실제 stdout을 확인한다."""
        if self.target.environment != "staging" or not self.target.reset_task_family:
            raise StagingError("staging 전용 reset task가 필요하다")
        current = (await self._call("ecs", "describe_task_definition",
                                    taskDefinition=self.target.reset_task_family))["taskDefinition"]
        spec = {key: current[key] for key in _REGISTER_KEYS if key in current}
        containers = []
        selected = None
        for container in current.get("containerDefinitions", []):
            if container.get("name") == self.target.reset_container_name:
                if selected is not None:
                    raise StagingError("reset 컨테이너 이름이 중복됐다")
                env = {item["name"]: item.get("value") for item in container.get("environment", [])}
                if (env.get("APP_ENV") != "staging" or env.get("STORAGE_BACKEND") != "postgres"
                        or env.get("APP_RESET_DATABASE") != self.target.database_name
                        or env.get("PGDATABASE") != self.target.database_name):
                    raise StagingError("reset task의 staging DB 식별·허용 설정이 필요하다")
                container = {**container, "image": req.images["backend"], "entryPoint": ["node"],
                             "command": ["scripts/reset-db.mjs"], "essential": True}
                selected = container
            containers.append(container)
        if selected is None:
            raise StagingError("전용 reset 컨테이너가 없다")
        log = selected.get("logConfiguration", {})
        options = log.get("options", {})
        if (log.get("logDriver") != "awslogs" or not options.get("awslogs-group")
                or not options.get("awslogs-stream-prefix")
                or options.get("awslogs-region", self.target.region) != self.target.region):
            raise StagingError("reset의 구조화 출력을 확인할 같은 리전 awslogs 설정이 필요하다")
        registered = await self._call("ecs", "register_task_definition", **{**spec, "containerDefinitions": containers},
                                      tags=[{"key": "DeltaRunId", "value": req.run_id}])
        revision = registered["taskDefinition"]["taskDefinitionArn"]
        service = (await self._call("ecs", "describe_services", cluster=self.target.cluster,
                                    services=[self.target.service]))["services"][0]
        network = service.get("networkConfiguration")
        if not network or not network.get("awsvpcConfiguration"):
            raise StagingError("reset task가 사용할 staging 서비스 네트워크 설정이 없다")
        placement = ({"capacityProviderStrategy": service["capacityProviderStrategy"]}
                     if service.get("capacityProviderStrategy") else {"launchType": service.get("launchType", "FARGATE")})
        run = await self._call("ecs", "run_task", cluster=self.target.cluster, taskDefinition=revision,
                               networkConfiguration=network, count=1, startedBy=req.run_id[:36], **placement)
        if run.get("failures") or len(run.get("tasks", [])) != 1:
            raise StagingError("초기화 ECS task를 시작하지 못했다")
        arn = run["tasks"][0]["taskArn"]
        deadline = time.monotonic() + self.reset_timeout
        while True:
            response = await self._call("ecs", "describe_tasks", cluster=self.target.cluster, tasks=[arn])
            tasks = response.get("tasks", [])
            if response.get("failures"):
                raise StagingError("초기화 ECS task를 조회하지 못했다")
            task = next((t for t in tasks if t.get("taskArn") == arn and t.get("taskDefinitionArn") == revision), {})
            if task.get("lastStatus") == "STOPPED":
                reset_containers = [c for c in task.get("containers", []) if c.get("name") == self.target.reset_container_name]
                if (len(reset_containers) != 1 or type(reset_containers[0].get("exitCode")) is not int
                        or reset_containers[0]["exitCode"] != 0
                        or reset_containers[0].get("imageDigest") != req.image_digests["backend"]):
                    raise StagingError("초기화 task의 종료 코드 또는 실제 BE digest가 일치하지 않는다")
                break
            if time.monotonic() >= deadline:
                raise StagingError("초기화 ECS task 시간 초과. task 종료 여부를 운영자가 확인해야 한다")
            await asyncio.sleep(self.poll_seconds)
        stream = f"{options['awslogs-stream-prefix']}/{self.target.reset_container_name}/{arn.rsplit('/', 1)[-1]}"
        while True:
            try:
                messages = []
                token = None
                while True:
                    args = {"logGroupName": options["awslogs-group"], "logStreamName": stream,
                            "startFromHead": True, "limit": 1000}
                    if token:
                        args["nextToken"] = token
                    logs = await self._call("logs", "get_log_events", **args)
                    messages.extend(event.get("message", "") for event in logs.get("events", []))
                    next_token = logs.get("nextForwardToken")
                    if not next_token or next_token == token:
                        break
                    if time.monotonic() >= deadline:
                        raise StagingError("초기화 task 로그 페이지 조회 시간 초과")
                    token = next_token
            except Exception as exc:
                if isinstance(exc, StagingError):
                    raise
                code = getattr(exc, "response", {}).get("Error", {}).get("Code")
                if code != "ResourceNotFoundException":
                    raise StagingError("초기화 task 로그 조회 실패") from None
                messages = []
            result = reset_result(messages)
            if result is not None:
                return result
            if time.monotonic() >= deadline:
                raise StagingError("초기화 task의 구조화된 잔존 행·DB18 출력이 없다")
            await asyncio.sleep(self.poll_seconds)

    async def _rollout_failed(self, revision: str) -> None:
        services = (await self._call("ecs", "describe_services", cluster=self.target.cluster,
                                     services=[self.target.service]))["services"]
        deployment = next((d for d in services[0]["deployments"] if d["taskDefinition"] == revision), None)
        if deployment is not None and deployment.get("rolloutState") == "FAILED":
            raise StagingError(f"ECS 배포 실패: {cloud_failure(deployment.get('rolloutStateReason'))}")

    async def _new_task(self, revision: str, req: DeployRequest | None = None) -> tuple[str, str, str]:
        """새 개정판으로 실행 중인 태스크와 ECS가 실제로 받은 이미지 digest(요청 값이 아니다), 태스크 IP."""
        deadline = time.monotonic() + self.rollout_timeout
        while True:
            await self._rollout_failed(revision)
            arns = (await self._call("ecs", "list_tasks", cluster=self.target.cluster, serviceName=self.target.service,
                                     desiredStatus="RUNNING"))["taskArns"]
            tasks = (await self._call("ecs", "describe_tasks", cluster=self.target.cluster, tasks=arns))["tasks"] if arns else []
            for task in tasks:
                if task.get("taskDefinitionArn") != revision or task.get("lastStatus") != "RUNNING":
                    continue
                if req is not None and req.images:
                    names = (self.target.container_name, self.target.backend_container_name)
                    apps = [c for c in task.get("containers", []) if c.get("name") in names]
                    if (len(apps) != 2 or len({c.get("name") for c in apps}) != 2
                            or any(c.get("lastStatus") != "RUNNING" or not DIGEST.fullmatch(c.get("imageDigest", ""))
                                   for c in apps)):
                        continue
                container = next((c for c in task.get("containers", []) if c.get("name") == self.target.container_name), {})
                digest = container.get("imageDigest", "")
                ip = next((n.get("privateIpv4Address") for n in container.get("networkInterfaces", [])), None)
                if DIGEST.match(digest) and ip:
                    return task["taskArn"], digest, ip
            if time.monotonic() >= deadline:
                raise StagingError(f"새 태스크가 {int(self.rollout_timeout)}초 안에 실행되지 않았다")
            await asyncio.sleep(self.poll_seconds)

    async def _wait_healthy(self, ip: str) -> None:
        """새 태스크가 healthy 이고 다른 태스크는 트래픽을 받지 않을 때까지. DAST 가 이전 버전을 보지 않게 한다."""
        deadline = time.monotonic() + self.health_timeout
        while True:
            targets = (await self._call("elbv2", "describe_target_health",
                                        TargetGroupArn=self.target.target_group_arn))["TargetHealthDescriptions"]
            new = [t for t in targets if t.get("Target", {}).get("Id") == ip]
            old = [t for t in targets if t.get("Target", {}).get("Id") != ip and t["TargetHealth"]["State"] == "healthy"]
            if any(t["TargetHealth"]["State"] == "healthy" for t in new) and not old:
                return
            if time.monotonic() >= deadline:
                states = ", ".join(f"{t.get('Target', {}).get('Id', '?')}={t['TargetHealth']['State']}"
                                   for t in targets) or "대상 없음"
                raise StagingError(f"헬스 체크가 {int(self.health_timeout)}초 안에 통과하지 않았다 ({states})")
            await asyncio.sleep(self.poll_seconds)

    async def _scanner_rules(self) -> list[dict[str, Any]]:
        rules = await self._call("ec2", "describe_security_group_rules", Filters=[
            {"Name": "group-id", "Values": [self.target.alb_security_group_id]},
            {"Name": f"tag:{SCANNER_TAG['Key']}", "Values": [SCANNER_TAG["Value"]]},
        ])
        return [rule for rule in rules["SecurityGroupRules"] if not rule.get("IsEgress")]

    async def _allow_scanner(self, ip: str, run_id: str) -> None:
        """공개 ALB 에는 지금 실행기 IP 하나만 열어 둔다. 이전 실행의 IP 는 지운다."""
        cidr = f"{ip}/32"
        rules = await self._scanner_rules()
        stale = [rule["SecurityGroupRuleId"] for rule in rules if rule.get("CidrIpv4") != cidr]
        if stale:
            await self._call("ec2", "revoke_security_group_ingress", GroupId=self.target.alb_security_group_id,
                             SecurityGroupRuleIds=stale)
        if any(rule.get("CidrIpv4") == cidr for rule in rules):
            return
        await self._call("ec2", "authorize_security_group_ingress", GroupId=self.target.alb_security_group_id,
                         IpPermissions=[{"IpProtocol": "tcp", "FromPort": 80, "ToPort": 80,
                                         "IpRanges": [{"CidrIp": cidr, "Description": f"delta-staging {run_id}"}]}],
                         TagSpecifications=[{"ResourceType": "security-group-rule",
                                             "Tags": [SCANNER_TAG, {"Key": "DeltaRunId", "Value": run_id}]}])

    async def stop(self) -> None:
        await self._call("ecs", "update_service", cluster=self.target.cluster, service=self.target.service,
                         desiredCount=0)
        if self.target.dynamic_scanner_access:  # 내린 뒤에는 공개 ALB 에 열린 IP 도 닫는다
            rules = await self._scanner_rules()
            if rules:
                await self._call("ec2", "revoke_security_group_ingress", GroupId=self.target.alb_security_group_id,
                                 SecurityGroupRuleIds=[rule["SecurityGroupRuleId"] for rule in rules])
