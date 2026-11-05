"""GCP Cloud Run 스테이징. 기반(서비스·외부 LB·Cloud Armor)은 Terraform 이 만들고, 여기서는 이미지만 바꾼다.

순서: (Cloud Armor 에 스캐너 IP 허용) → 이미지 digest 확정 → (데이터 초기화 Job: 같은 이미지로 마이그레이션·비우기)
→ 서비스 템플릿 교체(새 개정판)
→ 새 개정판이 준비되고 트래픽 100% 를 받을 때 → (실행기에서 LB 주소 응답 확인) → ready.
Cloud Run v2 개정판에는 실제로 받은 digest 필드가 없다. 그래서 태그는 Artifact Registry 에서 digest 로 바꾼 뒤
그 digest 로 개정판을 만든다. 개정판이 digest 로 고정되므로 다른 이미지를 실행할 수 없다.
"""

from __future__ import annotations

import asyncio
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import quote

from delta_staging.aws_ecs import Outcome, Report, StagingError, current_public_ip
from delta_staging.config import GcpTarget
from delta_staging.contract import DeployRequest

RUN = "https://run.googleapis.com/v2"
COMPUTE = "https://compute.googleapis.com/compute/v1"
REGISTRY = "https://artifactregistry.googleapis.com/v1"
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
            try:
                message = response.json().get("error", {}).get("message", "")
            except ValueError:
                message = ""
            raise StagingError(f"Google API {response.status_code}: {message or response.reason}")
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
    def __init__(self, target: GcpTarget, *, http: Http | None = None, poll_seconds: float = 3,
                 rollout_timeout: float = 600, health_timeout: float = 180, reset_timeout: float = 240,
                 public_ip: Callable[[], str] = current_public_ip, probe: Callable[[str], bool] = _probe) -> None:
        self.target = target
        self._http = http
        self.poll_seconds = poll_seconds
        self.rollout_timeout = rollout_timeout
        self.health_timeout = health_timeout
        self.reset_timeout = reset_timeout
        self.public_ip = public_ip
        self.probe = probe

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
        self.check_image(req.image)
        url = await self.endpoint()
        scanner_ip = None
        if req.scanner_ip:
            scanner_ip = await asyncio.to_thread(self.public_ip) if req.scanner_ip == "auto" else req.scanner_ip
            await self._allow_scanner(scanner_ip, req.run_id)
            report("scanner_allowed", f"{scanner_ip}/32")
        image = await self._pinned(req.image)
        digest = image.rsplit("@", 1)[1]
        report("image_digest", digest)
        if self.target.reset_job:
            await self._reset_data(image)
            report("data_reset", f"{self.target.reset_job}: 남은 행 0")
        operation = await self._update_service(req, image)
        report("service_updated", self.target.service)
        await self._wait_run_operation(operation)
        revision = await self._serving_revision(req.run_id)
        report("traffic_switched", _short(revision))
        await self._check_revision_image(revision, image)
        if req.scanner_ip == "auto":  # 도구가 곧 스캐너일 때만 LB·Cloud Armor 를 거친 실제 응답을 확인할 수 있다
            await self._wait_reachable(url)
            report("healthy", url)
        else:
            report("healthy", "Cloud Run 개정판 준비 완료")
        return Outcome(url=url, revision=revision, image_digest=digest, task_arn=revision,
                       scanner_ip=scanner_ip)

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
        for container in template.get("containers", []):
            if container.get("name") == self.target.container_name:
                found = True
                env = {**req.env, "DELTA_RUN_ID": req.run_id, **({"DELTA_COMMIT": req.commit} if req.commit else {})}
                kept = [item for item in container.get("env", []) if item["name"] not in env]
                container = {**container, "image": image,
                             "env": kept + [{"name": k, "value": v} for k, v in sorted(env.items())]}
            containers.append(container)
        if not found:
            raise StagingError(f"Cloud Run 서비스에 컨테이너 {self.target.container_name} 이 없다")
        template.pop("revision", None)  # 개정판 이름은 Cloud Run 이 새로 짓는다
        template["labels"] = {**template.get("labels", {}), RUN_LABEL: _label(req.run_id)}
        template["containers"] = containers
        return await self._call("PATCH", f"{RUN}/{self.service_name}", body={**service, "template": template})

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

    async def _wait_operation(self, operation: dict[str, Any], timeout: float, what: str) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while not operation.get("done"):
            if time.monotonic() >= deadline:
                raise StagingError(f"{what}이(가) {int(timeout)}초 안에 끝나지 않았다")
            await asyncio.sleep(self.poll_seconds)
            operation = await self._call("GET", f"{RUN}/{operation['name']}")
        if operation.get("error"):
            raise StagingError(f"{what} 실패: {operation['error'].get('message', '사유 없음')}")
        return operation.get("response", {})

    async def _wait_run_operation(self, operation: dict[str, Any]) -> None:
        deadline = time.monotonic() + self.rollout_timeout
        while not operation.get("done"):
            if time.monotonic() >= deadline:
                raise StagingError(f"새 개정판이 {int(self.rollout_timeout)}초 안에 준비되지 않았다")
            await asyncio.sleep(self.poll_seconds)
            operation = await self._call("GET", f"{RUN}/{operation['name']}")
        if operation.get("error"):
            raise StagingError(f"Cloud Run 배포 실패: {operation['error'].get('message', '사유 없음')}")

    async def _serving_revision(self, run_id: str) -> str:
        """이번 실행이 만든 개정판이 준비됐고 트래픽 100% 를 받는지. DAST 가 이전 버전을 보지 않게 한다."""
        deadline = time.monotonic() + self.health_timeout
        while True:
            service = await self._call("GET", f"{RUN}/{self.service_name}")
            condition = service.get("terminalCondition", {})
            if condition.get("state") == "CONDITION_FAILED":
                raise StagingError(f"Cloud Run 배포 실패: {condition.get('message', '사유 없음')}")
            created = service.get("latestCreatedRevision", "")
            if not service.get("reconciling") and created and _short(created) == _short(service.get("latestReadyRevision", "")):
                revision = await self._call("GET", f"{RUN}/{self.service_name}/revisions/{_short(created)}")
                if revision.get("labels", {}).get(RUN_LABEL) != _label(run_id):
                    raise StagingError("최신 개정판이 이번 실행의 것이 아니다. 다른 배포가 끼어들었다")
                percent = sum(t.get("percent", 0) for t in service.get("trafficStatuses", [])
                              if _short(t.get("revision", "")) == _short(created)
                              or (t.get("type") == "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST" and not t.get("revision")))
                if percent == 100:
                    return revision.get("name") or created
            if time.monotonic() >= deadline:
                raise StagingError(f"새 개정판이 {int(self.health_timeout)}초 안에 트래픽을 모두 받지 않았다")
            await asyncio.sleep(self.poll_seconds)

    async def _check_revision_image(self, revision: str, image: str) -> None:
        current = await self._call("GET", f"{RUN}/{self.service_name}/revisions/{_short(revision)}")
        images = [c.get("image") for c in current.get("containers", []) if c.get("name") == self.target.container_name]
        if images != [image]:
            raise StagingError("개정판의 이미지가 요청과 다르다")

    async def _wait_reachable(self, base: str) -> None:
        url = base + self.target.health_check_path
        deadline = time.monotonic() + self.health_timeout
        while not await asyncio.to_thread(self.probe, url):
            if time.monotonic() >= deadline:
                raise StagingError(f"LB 주소가 {int(self.health_timeout)}초 안에 응답하지 않았다(Cloud Armor 반영 지연 포함)")
            await asyncio.sleep(self.poll_seconds)

    async def _scanner_rule(self) -> dict[str, Any] | None:
        policy = await self._call("GET", self.policy_url)
        return next((r for r in policy.get("rules", []) if r.get("priority") == self.target.scanner_rule_priority), None)

    async def _allow_scanner(self, ip: str, run_id: str) -> None:
        """Cloud Armor 에는 지금 실행기 IP 하나만 열어 둔다. 이전 실행의 IP 는 같은 규칙을 덮어써서 지운다."""
        cidr = f"{ip}/32"
        current = await self._scanner_rule()
        if current is not None and current.get("match", {}).get("config", {}).get("srcIpRanges") == [cidr]:
            return
        rule = {"priority": self.target.scanner_rule_priority, "action": "allow", "description": f"delta-staging {run_id}",
                "match": {"versionedExpr": "SRC_IPS_V1", "config": {"srcIpRanges": [cidr]}}}
        if current is None:
            operation = await self._call("POST", f"{self.policy_url}/addRule", body=rule)
        else:
            operation = await self._call("POST", f"{self.policy_url}/patchRule",
                                         params={"priority": self.target.scanner_rule_priority}, body=rule)
        await self._wait_compute_operation(operation)

    async def _wait_compute_operation(self, operation: dict[str, Any]) -> None:
        deadline = time.monotonic() + self.rollout_timeout
        while operation.get("status") != "DONE":
            if time.monotonic() >= deadline:
                raise StagingError("Cloud Armor 규칙 변경이 끝나지 않았다")
            await asyncio.sleep(self.poll_seconds)
            operation = await self._call(
                "GET", f"{COMPUTE}/projects/{self.target.project_id}/global/operations/{operation['name']}")
        if operation.get("error"):
            errors = operation["error"].get("errors", [])
            raise StagingError(f"Cloud Armor 규칙 변경 실패: {errors[0].get('message', '') if errors else '사유 없음'}")

    async def stop(self) -> None:
        """스캐너 접근을 닫는다. Cloud Run 은 요청이 없으면 최소 인스턴스만 남고, 닿을 수 있는 경로가 없다."""
        if not self.target.dynamic_scanner_access:
            return
        if await self._scanner_rule() is not None:
            operation = await self._call("POST", f"{self.policy_url}/removeRule",
                                         params={"priority": self.target.scanner_rule_priority})
            await self._wait_compute_operation(operation)
