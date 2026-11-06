"""GCP Cloud Run 스테이징. 기반(서비스·외부 LB·Cloud Armor)은 Terraform 이 만들고, 여기서는 이미지만 바꾼다.

순서: (Cloud Armor 에 스캐너 IP 허용) → 이미지 digest 확정 → (데이터 초기화 Job: 같은 이미지로 마이그레이션·비우기)
→ 서비스 템플릿 교체(새 개정판)
→ 새 개정판이 준비되고 트래픽 100% 를 받을 때 → (실행기에서 LB 주소 응답 확인) → ready.
Cloud Run v2 개정판에는 실제로 받은 digest 필드가 없다. 그래서 태그는 Artifact Registry 에서 digest 로 바꾼 뒤
그 digest 로 개정판을 만든다. 개정판이 digest 로 고정되므로 다른 이미지를 실행할 수 없다.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import quote

from delta_staging.aws_ecs import (Outcome, Report, StagingError, check_health, current_public_ip,
                                   cloud_failure, health_json, reset_result, SafetyCheck, verify_scan_safety)
from delta_staging.config import GcpTarget
from delta_staging.contract import DeployRequest, bundle_hash

RUN = "https://run.googleapis.com/v2"
COMPUTE = "https://compute.googleapis.com/compute/v1"
REGISTRY = "https://artifactregistry.googleapis.com/v1"
LOGGING = "https://logging.googleapis.com/v2"
SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)
AR_IMAGE = re.compile(r"^(?P<host>(?P<location>[a-z0-9-]+)-docker\.pkg\.dev)/(?P<project>[a-z0-9-]+)/(?P<repo>[a-z0-9._-]+)"
                      r"/(?P<package>[a-z0-9._/-]+?)(:(?P<tag>[A-Za-z0-9_.-]{1,128}))?$")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}$")
RUN_LABEL = "delta-run-id"


class Http(Protocol):
    def request(self, method: str, url: str, *, params: dict[str, Any] | None = None,
                body: dict[str, Any] | None = None) -> dict[str, Any]: ...


class GoogleHttp:
    """ADC(gcloud auth application-default login, 서비스 계정, 워크로드 아이덴티티)로 Google API 를 부른다."""

    def __init__(self) -> None:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        credentials, _ = google.auth.default(scopes=SCOPES)
        self.session = AuthorizedSession(credentials)

    def request(self, method: str, url: str, *, params: dict[str, Any] | None = None,
                body: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.session.request(method, url, params=params, json=body, timeout=30)
        if response.status_code >= 400:
            raise StagingError(f"Google API HTTP {response.status_code}. 응답 원문은 비밀 보호를 위해 생략했다")
        return response.json() if response.content else {}


def _short(name: str) -> str:
    return name.rsplit("/", 1)[-1]


def _label(value: str) -> str:
    """GCP 라벨 값 규칙(소문자·숫자·_·-, 63자)에 맞춘다."""
    return re.sub(r"[^a-z0-9_-]", "-", value.lower())[:63]


def _probe(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310 설정의 스테이징 주소
            return response.status < 400
    except (urllib.error.URLError, TimeoutError):
        return False


class GcpCloudRunStaging:
    def __init__(self, target: GcpTarget, *, http: Http | None = None, poll_seconds: float = 1,
                 rollout_timeout: float = 600, health_timeout: float = 180, reset_timeout: float = 240,
                 public_ip: Callable[[], str] = current_public_ip, probe: Callable[[str], bool] = _probe,
                 stable_probes: int = 3, health_probe: Callable[[str], dict[str, Any]] = health_json,
                 safety_check: SafetyCheck | None = None) -> None:
        self.target = target
        self._http = http
        self.poll_seconds = poll_seconds
        self.rollout_timeout = rollout_timeout
        self.health_timeout = health_timeout
        self.reset_timeout = reset_timeout
        self.public_ip = public_ip
        self.probe = probe
        self.stable_probes = stable_probes  # 준비로 보려면 연속으로 성공해야 하는 응답 수
        self.health_probe = health_probe
        self.safety_check = safety_check
        self._schema_prepared = target.schema_prepared

    @property
    def service_name(self) -> str:
        return f"projects/{self.target.project_id}/locations/{self.target.region}/services/{self.target.service}"

    @property
    def job_name(self) -> str:
        return f"projects/{self.target.project_id}/locations/{self.target.region}/jobs/{self.target.reset_job}"

    @property
    def policy_url(self) -> str:
        return f"{COMPUTE}/projects/{self.target.project_id}/global/securityPolicies/{self.target.security_policy}"

    async def _call(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        if self._http is None:
            self._http = await asyncio.to_thread(GoogleHttp)
        return await asyncio.to_thread(self._http.request, method, url, **kwargs)

    def check_request(self, req: DeployRequest) -> None:
        self.check_image(req.image)
        for image in (req.images or {}).values():
            self.check_image(image)
        if req.images and (self.target.environment != "staging" or not self.target.backend_container_name
                           or not self.target.reset_job or not self.target.database_name):
            raise StagingError("v2에는 staging의 BE·reset Job·database_name 설정이 필요하다")
        if req.images and self.safety_check is None:
            raise StagingError("v2에는 S06 관측 훅 연결이 필요하다. 라이브러리 호출자가 제공해야 한다")
        if req.scanner_ip and not self.target.dynamic_scanner_access:
            raise StagingError("이 스테이징은 고정 스캐너 대역만 받아서 scanner_ip 를 받지 않는다")

    def check_image(self, image: str) -> None:
        if not any(image.startswith(prefix) for prefix in self.target.allowed_image_prefixes):
            raise StagingError(f"허용하지 않은 저장소의 이미지다. 허용: {', '.join(self.target.allowed_image_prefixes)}")

    async def endpoint(self) -> str:
        """스테이징 주소. 전역 IP 를 이름으로 매번 조회한다."""
        if self.target.url:
            return self.target.url.rstrip("/")
        found = await self._call("GET", f"{COMPUTE}/projects/{self.target.project_id}/global/addresses/{self.target.address}")
        if not found.get("address"):
            raise StagingError(f"전역 IP {self.target.address} 에 주소가 없다")
        return f"http://{found['address']}"

    async def deploy(self, req: DeployRequest, report: Report) -> Outcome:
        self.check_request(req)
        if req.images:
            await verify_scan_safety(self.safety_check, req, self.target.database_name)
            report("scan_safety", "이전 DAST 종료·교체 전 쓰기 drain 확인")
        url = await self.endpoint()
        scanner_ip, rule_changed = None, False
        if req.scanner_ip:
            scanner_ip = await asyncio.to_thread(self.public_ip) if req.scanner_ip == "auto" else req.scanner_ip
            rule_changed = await self._allow_scanner(scanner_ip, req.run_id)
            report("scanner_allowed", f"{scanner_ip}/32")
        if req.images:
            return await self._deploy_bundle(req, report, url, scanner_ip, rule_changed)
        image = await self._pinned(req.image)
        digest = image.rsplit("@", 1)[1]
        report("image_digest", digest)
        # 데이터 초기화(Job)와 새 개정판 배포를 동시에 돌린다. 앱은 기동할 때 스키마·데이터를 건드리지 않고(스키마는 초기화 Job 이
        # 적용), 점검은 둘 다 끝난 뒤에 시작하므로 결과는 순차와 같다. 초기화가 실패하면 배포 전체를 실패로 끝낸다(점검하지 않는다).
        reset = asyncio.create_task(self._reset_data(image)) if self.target.reset_job else None
        try:
            operation = await self._update_service(req, image)
            report("service_updated", self.target.service)
            await self._wait_run_operation(operation)
            revision = await self._serving_revision(req.run_id)
            report("traffic_switched", _short(revision))
            await self._check_revision_image(revision, image)
        except BaseException:
            if reset is not None:
                reset.cancel()
                await asyncio.gather(reset, return_exceptions=True)
            raise
        if reset is not None:
            await reset
            report("data_reset", f"{self.target.reset_job}: 남은 행 0")
        if req.scanner_ip == "auto":  # 도구가 곧 스캐너일 때만 LB·Cloud Armor 를 거친 실제 응답을 확인할 수 있다
            # Cloud Armor 규칙을 이번에 바꿨을 때만 에지 반영 흔들림을 연속 응답으로 확인한다. 그대로면 한 번이면 된다
            await self._wait_reachable(url, self.stable_probes if rule_changed else 1)
            report("healthy", url)
        else:
            report("healthy", "Cloud Run 개정판 준비 완료")
        return Outcome(url=url, revision=revision, image_digest=digest, task_arn=revision,
                       scanner_ip=scanner_ip)

    async def _deploy_bundle(self, req: DeployRequest, report: Report, url: str,
                             scanner_ip: str | None, rule_changed: bool) -> Outcome:
        # 둘 다 실제로 존재하는지 먼저 읽는다. 구성 누락이면 DB를 변경하지 않는다.
        service = await self._call("GET", f"{RUN}/{self.service_name}")
        names = [c.get("name") for c in service.get("template", {}).get("containers", [])]
        if names.count(self.target.container_name) != 1 or names.count(self.target.backend_container_name) != 1:
            raise StagingError("Cloud Run의 FE·BE 두 컨테이너 구성이 필요하다")
        backend = next(c for c in service["template"]["containers"] if c.get("name") == self.target.backend_container_name)
        backend_env = {item["name"]: item.get("value") for item in backend.get("env", [])}
        if backend_env.get("PGDATABASE") != self.target.database_name:
            raise StagingError("BE 서비스 정의의 DB 이름이 staging target과 다르다")
        reset = None
        if not self._schema_prepared:
            result = await self._reset_bundle(req)
            self._schema_prepared = True
            report("schema_prepared", "최초 스키마 준비와 초기화 관측 완료")
        else:
            reset = asyncio.create_task(self._reset_bundle(req, observe_logs=False))
        try:
            operation = await self._update_service(req, req.image)
            report("service_updated", self.target.service)
            await self._wait_run_operation(operation)
            revision = await self._serving_revision(req.run_id, strict=True)
            report("traffic_switched", _short(revision))
            images = await self._check_bundle_revision(revision, req)
            observed = {role: image.rsplit("@", 1)[1] for role, image in images.items()}
            report("image_digest", json.dumps(observed, sort_keys=True))
            if reset is not None:
                result = await reset
            if req.scanner_ip == "auto":
                await self._wait_reachable(url, self.stable_probes if rule_changed else 1)
            deadline = time.monotonic() + self.health_timeout
            while True:
                try:
                    version = check_health(await asyncio.to_thread(self.health_probe, url), req)
                    break
                except StagingError:
                    if time.monotonic() >= deadline:
                        raise
                await asyncio.sleep(self.poll_seconds)
            # 헬스 대기 중 다른 배포가 끼어들거나 traffic이 바뀐 경우도 차단한다.
            if await self._serving_revision(req.run_id, strict=True) != revision:
                raise StagingError("헬스 확인 중 serving revision이 바뀌었다")
            safety = await verify_scan_safety(self.safety_check, req, self.target.database_name, revision)
            report("data_reset", f"{self.target.reset_job}: 남은 행 {safety.remaining_rows}")
            report("healthy", "FE→BE→PostgreSQL18 확인")
            report("pre_dast_zero", "새 revision 전환 후 DAST 직전 행0 관측 확인")
            return Outcome(url, revision, observed["frontend"], revision, scanner_ip,
                           images=images, image_digests=observed, bundle_sha256=bundle_hash(req.commit, observed),
                           data_reset_remaining=(result["remaining_rows"] if result["remaining_rows"] is not None
                                                 else safety.remaining_rows), databaseVersionNum=version,
                           database_version_num=version, s06_verified=True, pre_dast_remaining=safety.remaining_rows)
        finally:
            if reset is not None:
                if not reset.done():
                    reset.cancel()
                await asyncio.gather(reset, return_exceptions=True)

    async def _check_bundle_revision(self, revision: str, req: DeployRequest) -> dict[str, str]:
        current = await self._call("GET", f"{RUN}/{self.service_name}/revisions/{_short(revision)}")
        if current.get("name") != revision:
            raise StagingError("관측한 개정판의 식별자가 다르다")
        images = {}
        for role, name in {"frontend": self.target.container_name, "backend": self.target.backend_container_name}.items():
            containers = [c for c in current.get("containers", []) if c.get("name") == name]
            if len(containers) != 1 or containers[0].get("image") != req.images[role]:
                raise StagingError("serving revision의 두 고정 이미지가 요청과 다르다")
            env = {item["name"]: item.get("value") for item in containers[0].get("env", [])}
            if env.get("DELTA_RUN_ID") != req.run_id:
                raise StagingError("serving revision의 정확한 실행 ID가 다르다")
            if role == "backend" and env.get("PGDATABASE") != self.target.database_name:
                raise StagingError("serving revision의 DB 이름이 staging target과 다르다")
            images[role] = containers[0]["image"]
        return images

    async def _pinned(self, image: str) -> str:
        """digest 로 고정한 이미지. 태그면 Artifact Registry 에서 지금 가리키는 digest 를 찾는다."""
        if "@sha256:" in image:
            return image
        match = AR_IMAGE.match(image)
        if match is None:
            raise StagingError("태그 이미지는 Artifact Registry 주소만 digest 로 바꿀 수 있다")
        package = quote(match["package"], safe="")
        tag = await self._call("GET", f"{REGISTRY}/projects/{match['project']}/locations/{match['location']}"
                                      f"/repositories/{match['repo']}/packages/{package}/tags/{match['tag'] or 'latest'}")
        digest = DIGEST.search(tag.get("version", ""))
        if digest is None:
            raise StagingError("태그가 가리키는 이미지 digest 를 찾지 못했다")
        return f"{match['host']}/{match['project']}/{match['repo']}/{match['package']}@{digest.group()}"

    async def _update_service(self, req: DeployRequest, image: str) -> dict[str, Any]:
        service = await self._call("GET", f"{RUN}/{self.service_name}")
        template = dict(service["template"])
        containers = []
        found = False
        found_backend = False
        for container in template.get("containers", []):
            frontend = container.get("name") == self.target.container_name
            backend = bool(req.images and container.get("name") == self.target.backend_container_name)
            if frontend or backend:
                found = found or frontend
                found_backend = found_backend or backend
                current_env = {item["name"]: item.get("value") for item in container.get("env", [])}
                if backend and current_env.get("PGDATABASE") != self.target.database_name:
                    raise StagingError("BE 서비스 정의의 DB 이름이 staging target과 다르다")
                env = {**req.env, "DELTA_RUN_ID": req.run_id, **({"DELTA_COMMIT": req.commit} if req.commit else {})}
                if req.images:
                    env.update({"APP_ENV": "staging", "APP_VERSION": req.commit, "CLOUD_PROVIDER": "gcp",
                                "IMAGE_DIGEST": req.image_digests["frontend" if frontend else "backend"]})
                    env.update({"API_HOST": "127.0.0.1", "API_PORT": "8080"} if frontend else {"STORAGE_BACKEND": "postgres"})
                kept = [item for item in container.get("env", []) if item["name"] not in env]
                container = {**container, "image": req.images["frontend" if frontend else "backend"] if req.images else image,
                             "env": kept + [{"name": k, "value": v} for k, v in sorted(env.items())]}
                if req.images:
                    container = {**container, "startupProbe": {"httpGet": {"path": "/healthz", "port": 3000 if frontend else 8080},
                                                              "periodSeconds": 2, "timeoutSeconds": 1, "failureThreshold": 60}}
                    if frontend:
                        container["ports"] = [{"containerPort": 3000}]
                        container["dependsOn"] = list(dict.fromkeys([*container.get("dependsOn", []), self.target.backend_container_name]))
                    else:
                        container.pop("ports", None)  # ingress는 FE에만 있다. Proxy 등 나머지 컨테이너는 유지한다.
            containers.append(container)
        if not found:
            raise StagingError(f"Cloud Run 서비스에 컨테이너 {self.target.container_name} 이 없다")
        if req.images and not found_backend:
            raise StagingError("Cloud Run 서비스에 BE 컨테이너가 없다")
        template.pop("revision", None)  # 개정판 이름은 Cloud Run 이 새로 짓는다
        template["labels"] = {**template.get("labels", {}), RUN_LABEL: _label(req.run_id)}
        template["containers"] = containers
        body = {**service, "template": template}
        if req.images:
            body["traffic"] = [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST", "percent": 100}]
        return await self._call("PATCH", f"{RUN}/{self.service_name}", body=body)

    async def _reset_data(self, image: str) -> None:
        """스테이징 DB를 비운다. 초기화 Job의 이미지를 배포할 이미지로 맞추고(같은 스키마로 마이그레이션) 실행한다.
        Job은 남은 행이 0이 아니면 실패로 끝난다. 실패하면 배포하지 않는다(이전 회차 페이로드가 판정을 오염시킨다)."""
        job = await self._call("GET", f"{RUN}/{self.job_name}")
        outer = dict(job["template"])
        inner = dict(outer["template"])
        inner["containers"] = [{**c, "image": image} for c in inner.get("containers", [])]
        if not inner["containers"]:
            raise StagingError(f"초기화 Job {self.target.reset_job} 에 컨테이너가 없다")
        update = await self._call("PATCH", f"{RUN}/{self.job_name}", body={**job, "template": {**outer, "template": inner}})
        await self._wait_operation(update, self.reset_timeout, "초기화 Job 갱신")
        run = await self._call("POST", f"{RUN}/{self.job_name}:run", body={})
        execution = await self._wait_operation(run, self.reset_timeout, "데이터 초기화")
        if execution.get("failedCount", 0) or not execution.get("succeededCount"):
            raise StagingError("스테이징 데이터 초기화가 실패했다(남은 데이터가 있거나 오류). 배포하지 않는다")

    async def _reset_bundle(self, req: DeployRequest, observe_logs: bool = True) -> dict[str, int | None]:
        if self.target.environment != "staging" or not self.target.reset_job:
            raise StagingError("staging 전용 reset Job이 필요하다")
        job = await self._call("GET", f"{RUN}/{self.job_name}")
        outer = dict(job["template"])
        inner = dict(outer["template"])
        containers = []
        found = False
        for container in inner.get("containers", []):
            if container.get("name") == self.target.reset_container_name:
                if found:
                    raise StagingError("reset 컨테이너 이름이 중복됐다")
                found = True
                env = {item["name"]: item.get("value") for item in container.get("env", [])}
                if (env.get("APP_ENV") != "staging" or env.get("STORAGE_BACKEND") != "postgres"
                        or env.get("APP_RESET_DATABASE") != self.target.database_name
                        or env.get("PGDATABASE") != self.target.database_name):
                    raise StagingError("reset Job의 staging DB 식별·허용 설정이 필요하다")
                container = {**container, "image": req.images["backend"],
                             "command": ["node", "scripts/reset-db.mjs"], "args": []}
            containers.append(container)
        if not found:
            raise StagingError("Job에 전용 reset 컨테이너가 없다")
        inner.update({"containers": containers, "maxRetries": 0})
        outer.update({"template": inner, "taskCount": 1, "parallelism": 1})
        update = await self._call("PATCH", f"{RUN}/{self.job_name}", body={**job, "template": outer})
        await self._wait_operation(update, self.reset_timeout, "초기화 Job 갱신")
        run = await self._call("POST", f"{RUN}/{self.job_name}:run", body={})
        execution = await self._wait_operation(run, self.reset_timeout, "데이터 초기화")
        name = execution.get("name", "")
        if (not name.startswith(self.job_name + "/executions/")
                or type(execution.get("succeededCount")) is not int or execution["succeededCount"] != 1
                or execution.get("failedCount", 0) != 0 or execution.get("cancelledCount", 0) != 0
                or execution.get("runningCount", 0) != 0):
            raise StagingError("초기화 Job 실행의 성공·식별 증거가 없다")
        executed = [c for c in execution.get("template", {}).get("containers", [])
                    if c.get("name") == self.target.reset_container_name]
        if len(executed) != 1 or executed[0].get("image") != req.images["backend"]:
            raise StagingError("초기화 execution의 실제 BE 이미지가 요청과 다르다")
        if not observe_logs:
            # 실행 성공·실행 이미지만 확인한다. 남은 행 0·PostgreSQL18 은 새 개정판 BE 헬스로 DAST 직전에 관측한다(S06).
            # Cloud Logging 수집 지연(약 20초)을 스테이징 시간에서 뺀다.
            return {"remaining_rows": None, "database_version_num": None}
        deadline = time.monotonic() + self.reset_timeout
        log_filter = (f'resource.type="cloud_run_job" AND resource.labels.job_name={json.dumps(self.target.reset_job)} '
                      f'AND resource.labels.location={json.dumps(self.target.region)} '
                      f'AND labels."run.googleapis.com/execution_name"={json.dumps(_short(name))} '
                      f'AND logName="projects/{self.target.project_id}/logs/run.googleapis.com%2Fstdout"')
        while True:
            messages = []
            page_token = None
            while True:
                body = {"resourceNames": [f"projects/{self.target.project_id}"],
                        "filter": log_filter, "pageSize": 1000, "orderBy": "timestamp asc"}
                if page_token:
                    body["pageToken"] = page_token
                logs = await self._call("POST", f"{LOGGING}/entries:list", body=body)
                for entry in logs.get("entries", []):
                    if isinstance(entry.get("jsonPayload"), dict):
                        messages.append(json.dumps(entry["jsonPayload"]))
                    elif isinstance(entry.get("textPayload"), str):
                        messages.append(entry["textPayload"])
                page_token = logs.get("nextPageToken")
                if not page_token:
                    break
                if time.monotonic() >= deadline:
                    raise StagingError("초기화 Job 로그 페이지 조회 시간 초과")
            result = reset_result(messages)
            if result is not None:
                return result
            if time.monotonic() >= deadline:
                raise StagingError("초기화 Job의 구조화된 잔존 행·DB18 출력이 없다")
            await asyncio.sleep(self.poll_seconds)

    async def _wait_operation(self, operation: dict[str, Any], timeout: float, what: str) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while not operation.get("done"):
            if time.monotonic() >= deadline:
                raise StagingError(f"{what}이(가) {int(timeout)}초 안에 끝나지 않았다")
            await asyncio.sleep(self.poll_seconds)
            operation = await self._call("GET", f"{RUN}/{operation['name']}")
        if operation.get("error"):
            raise StagingError(f"{what} 실패: {cloud_failure(operation['error'].get('message'))}")
        return operation.get("response", {})

    async def _wait_run_operation(self, operation: dict[str, Any]) -> None:
        deadline = time.monotonic() + self.rollout_timeout
        while not operation.get("done"):
            if time.monotonic() >= deadline:
                raise StagingError(f"새 개정판이 {int(self.rollout_timeout)}초 안에 준비되지 않았다")
            await asyncio.sleep(self.poll_seconds)
            operation = await self._call("GET", f"{RUN}/{operation['name']}")
        if operation.get("error"):
            raise StagingError(f"Cloud Run 배포 실패: {cloud_failure(operation['error'].get('message'))}")

    async def _serving_revision(self, run_id: str, *, strict: bool = False) -> str:
        """이번 실행이 만든 개정판이 준비됐고 트래픽 100% 를 받는지. DAST 가 이전 버전을 보지 않게 한다."""
        deadline = time.monotonic() + self.health_timeout
        while True:
            service = await self._call("GET", f"{RUN}/{self.service_name}")
            condition = service.get("terminalCondition", {})
            if condition.get("state") == "CONDITION_FAILED":
                raise StagingError(f"Cloud Run 배포 실패: {cloud_failure(condition.get('message'))}")
            created = service.get("latestCreatedRevision", "")
            ready = not strict or condition.get("state") == "CONDITION_SUCCEEDED"
            if ready and not service.get("reconciling") and created and _short(created) == _short(service.get("latestReadyRevision", "")):
                revision = await self._call("GET", f"{RUN}/{self.service_name}/revisions/{_short(created)}")
                if revision.get("labels", {}).get(RUN_LABEL) != _label(run_id):
                    raise StagingError("최신 개정판이 이번 실행의 것이 아니다. 다른 배포가 끼어들었다")
                percent = sum(t.get("percent", 0) for t in service.get("trafficStatuses", [])
                              if _short(t.get("revision", "")) == _short(created)
                              or (not strict and t.get("type") == "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST" and not t.get("revision")))
                total = sum(t.get("percent", 0) for t in service.get("trafficStatuses", []))
                if percent == 100 and (not strict or total == 100):
                    return revision.get("name") or created
            if time.monotonic() >= deadline:
                raise StagingError(f"새 개정판이 {int(self.health_timeout)}초 안에 트래픽을 모두 받지 않았다")
            await asyncio.sleep(self.poll_seconds)

    async def _check_revision_image(self, revision: str, image: str) -> None:
        current = await self._call("GET", f"{RUN}/{self.service_name}/revisions/{_short(revision)}")
        images = [c.get("image") for c in current.get("containers", []) if c.get("name") == self.target.container_name]
        if images != [image]:
            raise StagingError("개정판의 이미지가 요청과 다르다")

    async def _wait_reachable(self, base: str, needed: int) -> None:
        """LB 를 거친 응답을 연속으로 확인한다. Cloud Armor 규칙은 에지마다 다른 시점에 반영돼, 한 번 열린 뒤
        다음 요청이 403 일 수 있다(11/5 실제 실행: 준비 확인 직후 DAST 사전 확인이 403). 연속 성공을 봐야 준비로 본다."""
        url = base + self.target.health_check_path
        deadline = time.monotonic() + self.health_timeout
        streak = 0
        while streak < needed:
            streak = streak + 1 if await asyncio.to_thread(self.probe, url) else 0
            if streak >= needed:
                return
            if time.monotonic() >= deadline:
                raise StagingError(f"LB 주소가 {int(self.health_timeout)}초 안에 안정적으로 응답하지 않았다(Cloud Armor 반영 지연 포함)")
            await asyncio.sleep(self.poll_seconds)

    async def _scanner_rule(self) -> dict[str, Any] | None:
        policy = await self._call("GET", self.policy_url)
        return next((r for r in policy.get("rules", []) if r.get("priority") == self.target.scanner_rule_priority), None)

    async def _allow_scanner(self, ip: str, run_id: str) -> bool:
        """Cloud Armor 에는 지금 실행기 IP 하나만 열어 둔다. 이전 실행의 IP 는 같은 규칙을 덮어써서 지운다. 바꿨으면 True."""
        cidr = f"{ip}/32"
        current = await self._scanner_rule()
        if current is not None and current.get("match", {}).get("config", {}).get("srcIpRanges") == [cidr]:
            return False
        rule = {"priority": self.target.scanner_rule_priority, "action": "allow", "description": f"delta-staging {run_id}",
                "match": {"versionedExpr": "SRC_IPS_V1", "config": {"srcIpRanges": [cidr]}}}
        if current is None:
            operation = await self._call("POST", f"{self.policy_url}/addRule", body=rule)
        else:
            operation = await self._call("POST", f"{self.policy_url}/patchRule",
                                         params={"priority": self.target.scanner_rule_priority}, body=rule)
        await self._wait_compute_operation(operation)
        return True

    async def _wait_compute_operation(self, operation: dict[str, Any]) -> None:
        deadline = time.monotonic() + self.rollout_timeout
        while operation.get("status") != "DONE":
            if time.monotonic() >= deadline:
                raise StagingError("Cloud Armor 규칙 변경이 끝나지 않았다")
            await asyncio.sleep(self.poll_seconds)
            operation = await self._call(
                "GET", f"{COMPUTE}/projects/{self.target.project_id}/global/operations/{operation['name']}")
        if operation.get("error"):
            raise StagingError("Cloud Armor 규칙 변경 실패. 오류 상세 원문 생략")

    async def stop(self) -> None:
        """스캐너 접근을 닫는다. Cloud Run 은 요청이 없으면 최소 인스턴스만 남고, 닿을 수 있는 경로가 없다."""
        if not self.target.dynamic_scanner_access:
            return
        if await self._scanner_rule() is not None:
            operation = await self._call("POST", f"{self.policy_url}/removeRule",
                                         params={"priority": self.target.scanner_rule_priority})
            await self._wait_compute_operation(operation)
