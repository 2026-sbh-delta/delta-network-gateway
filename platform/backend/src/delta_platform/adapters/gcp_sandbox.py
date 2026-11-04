"""Owned, authenticated Cloud Run services with durable cleanup before completion."""

import json
import re
import time
from datetime import UTC, datetime
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import google.auth
from google.auth.transport.requests import AuthorizedSession
from pydantic import BaseModel, ConfigDict, Field, model_validator

from delta_platform.adapters.sandbox import SandboxError
from delta_platform.core.errors import LeaseLost, Problem

API = "https://run.googleapis.com/v2/"


class GcpTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    cloud: str = Field(default="gcp", pattern="^gcp$")
    project: str = Field(pattern=r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
    region: str = Field(default="asia-northeast3", pattern=r"^[a-z]+-[a-z]+[0-9]$")
    service_prefix: str = Field(default="delta-sbx", pattern=r"^delta-sbx[a-z0-9-]{0,8}$")
    repository: str
    runtime_service_account: str
    runner_service_account: str
    scanner_service_account: str
    network: str
    subnet: str
    network_tag: str = Field(default="delta-sandbox", pattern=r"^[a-z][a-z0-9-]{1,50}$")
    allowed_image_prefixes: list[str] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def owned_resources(self):
        if not re.fullmatch(
            rf"{re.escape(self.region)}-docker\.pkg\.dev/{re.escape(self.project)}/[a-z][a-z0-9-]+/[a-z][a-z0-9-]+",
            self.repository,
        ):
            raise ValueError("Expected an owned Artifact Registry image repository")
        for value in (
            self.runtime_service_account,
            self.runner_service_account,
            self.scanner_service_account,
        ):
            if not re.fullmatch(
                rf"[a-z][a-z0-9-]+@{re.escape(self.project)}\.iam\.gserviceaccount\.com", value
            ):
                raise ValueError("Service account project mismatch")
        if not re.fullmatch(
            rf"projects/{self.project}/global/networks/[a-z][a-z0-9-]+", self.network
        ):
            raise ValueError("Network project mismatch")
        if not re.fullmatch(
            rf"projects/{self.project}/regions/{self.region}/subnetworks/[a-z][a-z0-9-]+",
            self.subnet,
        ):
            raise ValueError("Subnet project/region mismatch")
        for value in self.allowed_image_prefixes:
            if not re.fullmatch(
                r"[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[a-z0-9/_-]+@", value
            ):
                raise ValueError("Expected an approved ECR repository")
        return self

    def resource(self, run_id):
        return f"projects/{self.project}/locations/{self.region}/services/{self.service_prefix}-{UUID(str(run_id)).hex}"


def configured_target(settings):
    if not settings.gcp_sandbox_target:
        raise Problem("sandbox_not_configured", "GCP 샌드박스가 아직 연결되지 않았습니다.", 503)
    try:
        return GcpTarget.model_validate_json(settings.gcp_sandbox_target).model_dump()
    except (ValueError, TypeError):
        raise Problem("sandbox_config_invalid", "GCP 샌드박스 설정을 확인하세요.", 503) from None


class CloudRun:
    def __init__(
        self, target, credentials_json="", *, session=None, poll_seconds=3, health_timeout=120
    ):
        self.target = target
        if session is None:
            credentials = (
                google.auth.load_credentials_from_dict(
                    json.loads(credentials_json),
                    scopes=["https://www.googleapis.com/auth/cloud-platform"],
                )[0]
                if credentials_json
                else google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])[
                    0
                ]
            )
            session = AuthorizedSession(credentials)
        self.session, self.poll_seconds = session, poll_seconds
        self.health_timeout = health_timeout

    def call(self, method, path, body=None, *, missing=False):
        try:
            response = self.session.request(
                method, API + path, json=body, timeout=25, allow_redirects=False
            )
            if missing and response.status_code == 404:
                return None
            if response.status_code == 409:
                raise SandboxError(
                    "sandbox_occupied",
                    "같은 이름의 GCP 실행이 이미 존재합니다. 소유권을 확인하세요.",
                )
            response.raise_for_status()
            return response.json()
        except SandboxError:
            raise
        except Exception:
            raise SandboxError(
                "sandbox_provider_error", "GCP 실행기 API에 접근하지 못했습니다."
            ) from None

    def wait(self, operation, guard, timeout=600):
        deadline = time.monotonic() + timeout
        while not operation.get("done"):
            guard()
            if time.monotonic() >= deadline:
                raise SandboxError("sandbox_timeout", "GCP 실행 작업이 제한 시간을 초과했습니다.")
            name = operation.get("name", "")
            if not name.startswith(
                f"projects/{self.target.project}/locations/{self.target.region}/operations/"
            ):
                raise SandboxError("sandbox_identity_mismatch", "GCP 작업 식별자가 다릅니다.")
            time.sleep(self.poll_seconds)
            operation = self.call("GET", name)
        if operation.get("error"):
            raise SandboxError(
                "sandbox_execution_failed", "GCP 실행 작업에 실패했습니다. 운영 로그를 확인하세요."
            )

    def get(self, resource):
        return self.call("GET", resource, missing=True)

    def owned(self, service, run_id, commit, image):
        target = self.target
        template = (service or {}).get("template", {})
        containers = template.get("containers", [])
        if (
            not service
            or service.get("name") != target.resource(run_id)
            or service.get("labels", {}).get("delta-run-id") != str(run_id)
            or service.get("labels", {}).get("delta-commit") != commit
            or template.get("serviceAccount") != target.runtime_service_account
            or len(containers) != 1
            or containers[0].get("image") != image
            or template.get("vpcAccess", {}).get("egress") != "ALL_TRAFFIC"
            or template.get("vpcAccess", {}).get("networkInterfaces")
            != [
                {
                    "network": target.network,
                    "subnetwork": target.subnet,
                    "tags": [target.network_tag],
                }
            ]
            or template.get("scaling", {}).get("maxInstanceCount") != 1
            or service.get("invokerIamDisabled", False)
        ):
            raise SandboxError(
                "sandbox_owner_changed",
                "GCP 실행 소유권·이미지·격리 설정이 다릅니다. 변경하지 않았습니다.",
            )

    def create(self, resource, run_id, commit, image, port, guard):
        target = self.target
        body = {
            "labels": {
                "delta-run-id": str(run_id),
                "delta-commit": commit,
                "managed-by": "delta-platform",
            },
            "ingress": "INGRESS_TRAFFIC_ALL",
            "invokerIamDisabled": False,
            "template": {
                "serviceAccount": target.runtime_service_account,
                "executionEnvironment": "EXECUTION_ENVIRONMENT_GEN2",
                "scaling": {"minInstanceCount": 0, "maxInstanceCount": 1},
                "maxInstanceRequestConcurrency": 1,
                "timeout": "60s",
                "vpcAccess": {
                    "egress": "ALL_TRAFFIC",
                    "networkInterfaces": [
                        {
                            "network": target.network,
                            "subnetwork": target.subnet,
                            "tags": [target.network_tag],
                        }
                    ],
                },
                "containers": [
                    {
                        "image": image,
                        "ports": [{"containerPort": port}],
                        "resources": {
                            "limits": {"cpu": "1", "memory": "512Mi"},
                            "cpuIdle": True,
                            "startupCpuBoost": False,
                        },
                        "env": [
                            {"name": "DELTA_RUN_ID", "value": str(run_id)},
                            {"name": "DELTA_COMMIT", "value": commit},
                        ],
                    }
                ],
            },
        }
        parent, name = resource.rsplit("/", 1)
        self.wait(self.call("POST", parent + "?serviceId=" + name, body), guard)

    def authorize(self, resource):
        self.call(
            "POST",
            resource + ":setIamPolicy",
            {
                "policy": {
                    "bindings": [
                        {
                            "role": "roles/run.invoker",
                            "members": [
                                "serviceAccount:" + self.target.runner_service_account,
                                "serviceAccount:" + self.target.scanner_service_account,
                            ],
                        }
                    ]
                }
            },
        )

    def id_token(self, url):
        response = self.session.post(
            "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
            + self.target.runner_service_account
            + ":generateIdToken",
            json={"audience": url, "includeEmail": True},
            timeout=20,
        )
        response.raise_for_status()
        return response.json()["token"]

    def verify(self, resource, run_id, commit, image, path, guard):
        import requests

        service = self.get(resource)
        if service is None:
            raise SandboxError("sandbox_missing", "GCP 실행 자원이 없습니다.")
        self.owned(service, run_id, commit, image)
        revision = service.get("latestReadyRevision")
        if (
            service.get("reconciling")
            or not revision
            or revision != service.get("latestCreatedRevision")
            or service.get("terminalCondition", {}).get("state") != "CONDITION_SUCCEEDED"
        ):
            raise SandboxError("sandbox_not_ready", "GCP 개정판의 준비 완료를 확인하지 못했습니다.")
        if not revision.startswith(resource + "/revisions/" + resource.rsplit("/", 1)[-1] + "-"):
            raise SandboxError("sandbox_identity_mismatch", "GCP 개정판 경계가 다릅니다.")
        actual = self.call("GET", revision)
        if actual.get("containers", [{}])[0].get("image") != image:
            raise SandboxError(
                "sandbox_identity_mismatch", "GCP 실제 개정판 이미지가 빌드와 다릅니다."
            )
        url = service.get("uri", "")
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.netloc.endswith(".run.app")
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
        ):
            raise SandboxError(
                "sandbox_identity_mismatch", "GCP가 반환한 실행 주소를 확인하지 못했습니다."
            )
        guard()
        token = self.id_token(url)
        # No Google access token is ever sent to the customer's container.
        deadline = time.monotonic() + self.health_timeout
        while True:
            guard()
            response = requests.get(
                url + path,
                headers={"X-Serverless-Authorization": "Bearer " + token},
                timeout=30,
                allow_redirects=False,
                stream=True,
            )
            status = response.status_code
            response.close()
            if 200 <= status < 400:
                break
            # Per-service IAM grants and the new revision may take time to propagate.
            if status not in (401, 403, 429, 502, 503) or time.monotonic() >= deadline:
                raise SandboxError(
                    "sandbox_health_failed",
                    f"GCP 앱의 HTTP 헬스 체크에 실패했습니다. (HTTP {status})",
                )
            time.sleep(self.poll_seconds)
        return {
            "revision": revision,
            "url": url,
            "resource": resource,
            "image_digest": image.rsplit("@", 1)[1],
        }

    def delete(self, resource, service, guard):
        suffix = "?" + urlencode({"etag": service["etag"]}) if service.get("etag") else ""
        op = self.call("DELETE", resource + suffix, missing=True)
        if op:
            self.wait(op, guard)
        if self.get(resource) is not None:
            raise SandboxError(
                "sandbox_cleanup_required", "GCP 샌드박스 삭제를 확인하지 못했습니다."
            )


class GcpSandboxRunner:
    def __init__(self, settings, *, provider=None, copier=None, inspector=None):
        self.target = configured_target(settings)
        self.config = GcpTarget.model_validate(self.target)
        self.provider = provider or CloudRun(self.config, settings.gcp_credentials_json)
        if copier is None:
            from delta_platform.adapters.registry_copy import RegistryCopy

            copier = RegistryCopy(self.config, self.provider.session)
        self.copier, self.inspector = copier, inspector

    def execute(self, claim, store, progress):
        with store.sandbox_lock() as acquired:
            if not acquired:
                raise LeaseLost()
            return self._execute(claim, store, progress)

    def _execute(self, claim, store, progress):
        run_id, token, source = claim["id"], claim["lease_token"], claim["input_snapshot"]
        if source["sandbox_target"] != self.target:
            progress(
                "sandbox_cleanup_wait",
                "GCP 실행 설정이 바뀌었습니다. 기존 설정을 복원해야 정리할 수 있습니다.",
            )
            raise LeaseLost()
        resource = self.config.resource(run_id)
        expected = self.config.repository + "@" + source["image_digest"]
        cleaning = False

        def guard():
            cancelled = store.sandbox_control(run_id, token)
            store.heartbeat(run_id, token)
            if cancelled and not cleaning:
                raise SandboxError("sandbox_cancelled", "GCP 샌드박스 중단을 요청했습니다.")

        outcome = claim["submission"]
        try:
            if not outcome:
                guard()
                if claim["attempt"] > 3:
                    raise SandboxError(
                        "sandbox_recovery_exhausted", "GCP 실행 복구 횟수를 초과했습니다."
                    )
                image = source["image"]
                if (
                    not re.fullmatch(r"sha256:[a-f0-9]{64}", source["image_digest"])
                    or not image.endswith("@" + source["image_digest"])
                    or image.rsplit("@", 1)[0] + "@" not in self.config.allowed_image_prefixes
                ):
                    raise SandboxError(
                        "sandbox_image_invalid", "허용된 빌드의 고정 이미지만 실행할 수 있습니다."
                    )
                # Durable deterministic identity precedes the remote create, including lost responses.
                if claim["external_id"] not in (None, resource):
                    raise SandboxError(
                        "sandbox_identity_mismatch", "저장된 GCP 실행 자원이 다릅니다."
                    )
                if not claim["external_id"]:
                    store.attach_sandbox(run_id, token, resource)
                current = self.provider.get(resource)
                if current is None:
                    progress("sandbox_prepare", "같은 이미지 digest를 GCP 저장소에 준비합니다.")
                    copied = self.copier.copy(image, guard)
                    if copied != expected:
                        raise SandboxError(
                            "sandbox_image_invalid", "복사한 이미지 digest가 다릅니다."
                        )
                    guard()
                    progress("sandbox_deploy", "GCP Cloud Run에서 격리된 앱을 실행합니다.")
                    self.provider.create(
                        resource,
                        run_id,
                        claim["resolved_commit_sha"],
                        expected,
                        source["container_port"],
                        guard,
                    )
                self.provider.owned(
                    self.provider.get(resource), run_id, claim["resolved_commit_sha"], expected
                )
                self.provider.authorize(resource)
                progress("sandbox_verify", "GCP 실제 개정판과 인증된 HTTP 응답을 확인합니다.")
                checked = self.provider.verify(
                    resource,
                    run_id,
                    claim["resolved_commit_sha"],
                    expected,
                    source["healthcheck_path"],
                    guard,
                )
                result = {
                    **checked,
                    "build_id": source["build_id"],
                    "image": image,
                    "runtime_image": expected,
                    "commit_sha": claim["resolved_commit_sha"],
                    "platform": "linux/amd64",
                    "runtime": "cloud_run",
                    "execution_cloud": "gcp",
                    "environment_check": "gcp_sandbox_only",
                    "runtime_check": "passed",
                    "security_check": "not_run",
                    "container_port": source["container_port"],
                    "healthcheck_path": source["healthcheck_path"],
                    "checked_at": datetime.now(UTC).isoformat(),
                }
                if self.inspector:
                    result.update(self.inspector.execute(claim, store, progress, guard, result))
                    self.provider.verify(
                        resource,
                        run_id,
                        claim["resolved_commit_sha"],
                        expected,
                        source["healthcheck_path"],
                        guard,
                    )
                outcome = store.sandbox_checkpoint(run_id, token, {"result": result})
        except LeaseLost:
            raise
        except Exception as exc:
            error = (
                exc.payload
                if isinstance(exc, SandboxError)
                else {
                    "code": "sandbox_provider_error",
                    "message": "GCP 샌드박스 실행에 실패했습니다. 원격 자원 정리를 확인합니다.",
                }
            )
            outcome = store.sandbox_checkpoint(run_id, token, {"error": error})
        cleaning = True
        try:
            stopped = self.inspector.stop(claim) if self.inspector else True
            guard()
            current = self.provider.get(resource)
            if current is not None:
                self.provider.owned(current, run_id, claim["resolved_commit_sha"], expected)
                progress("sandbox_cleanup", "이 실행이 만든 GCP 샌드박스를 삭제합니다.")
                self.provider.delete(resource, current, guard)
            if not stopped:
                raise SandboxError("dast_cleanup_required", "DAST 종료를 확인하지 못했습니다.")
        except LeaseLost:
            raise
        except Exception:
            progress(
                "sandbox_cleanup_wait",
                "GCP 실행·검사 정리를 확인하지 못해 공간을 잠갔습니다. 복구 후 재시도합니다.",
            )
            raise LeaseLost() from None
        if store.sandbox_control(run_id, token):
            raise SandboxError("sandbox_cancelled", "GCP 실행을 중단하고 삭제했습니다.")
        if "error" in outcome:
            raise SandboxError(**outcome["error"])
        progress("sandbox_complete", "GCP 실행·검사와 자원 삭제를 확인했습니다.")
        return {
            **outcome["result"],
            "cleanup": "stopped",
            "completed_at": datetime.now(UTC).isoformat(),
        }
