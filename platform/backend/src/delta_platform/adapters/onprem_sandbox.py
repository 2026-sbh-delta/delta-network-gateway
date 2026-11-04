"""Namespaced k3s execution; the worker never receives cluster-admin credentials."""

import base64
import ipaddress
import json
import re
import ssl
import time
from datetime import UTC, datetime
from uuid import UUID

import boto3
import httpx
from botocore.config import Config
from pydantic import BaseModel, ConfigDict, Field, model_validator

from delta_platform.adapters.sandbox import SandboxError
from delta_platform.core.errors import LeaseLost, Problem


class OnpremTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    cloud: str = Field(default="onprem", pattern="^onprem$")
    api_url: str
    ca_base64: str = Field(max_length=32768)
    namespace: str = Field(default="delta-platform-sandbox", pattern="^delta-platform-sandbox$")
    node_name: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{0,62}$")
    node_ip: str
    node_port: int = Field(default=32080, ge=30000, le=32767)
    registry_region: str = Field(default="ap-northeast-2", pattern=r"^[a-z]{2}-[a-z]+-[0-9]$")
    allowed_image_prefixes: list[str] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def validate_boundary(self):
        from urllib.parse import urlsplit

        url = urlsplit(self.api_url)
        if url.scheme != "https" or url.path or url.query or url.fragment or url.username:
            raise ValueError("Expected private Kubernetes HTTPS origin")
        for host in (url.hostname, self.node_ip):
            ip = ipaddress.ip_address(host)
            if ip.version != 4 or not ip.is_private or ip.is_unspecified or ip.is_link_local:
                raise ValueError("Expected a registered private IPv4 address")
        ssl.create_default_context(cadata=base64.b64decode(self.ca_base64, validate=True).decode())
        for prefix in self.allowed_image_prefixes:
            if not re.fullmatch(
                r"[0-9]{12}\.dkr\.ecr\."
                + re.escape(self.registry_region)
                + r"\.amazonaws\.com/[a-z0-9/_-]+@",
                prefix,
            ):
                raise ValueError("Expected approved ECR repository")
        return self

    def name(self, run_id):
        return "delta-sbx-" + UUID(str(run_id)).hex

    def resource(self, run_id):
        return f"namespaces/{self.namespace}/pods/{self.name(run_id)}"

    @property
    def url(self):
        return f"http://{self.node_ip}:{self.node_port}"


def configured_target(settings):
    if not settings.onprem_sandbox_target or not settings.onprem_sandbox_token.get_secret_value():
        raise Problem(
            "sandbox_not_configured", "온프레미스 실행기가 아직 연결되지 않았습니다.", 503
        )
    try:
        return OnpremTarget.model_validate_json(settings.onprem_sandbox_target).model_dump()
    except (ValueError, TypeError):
        raise Problem(
            "sandbox_config_invalid", "온프레미스 실행기 설정을 확인하세요.", 503
        ) from None


class K3s:
    def __init__(self, target, token, *, transport=None, ecr=None, poll_seconds=3, timeout=300):
        self.target, self.token, self.transport, self.ecr = target, token, transport, ecr
        self.poll_seconds, self.timeout = poll_seconds, timeout
        self.tls = ssl.create_default_context(cadata=base64.b64decode(target.ca_base64).decode())

    def call(self, method, collection, name=None, body=None, *, missing=False):
        if collection not in ("pods", "secrets", "services"):
            raise ValueError("Unsupported Kubernetes collection")
        path = f"/api/v1/namespaces/{self.target.namespace}/{collection}"
        if name:
            if not re.fullmatch(r"[a-z0-9-]+", name):
                raise ValueError("Invalid resource name")
            path += "/" + name
        with httpx.Client(
            verify=self.tls,
            transport=self.transport,
            timeout=15,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            with client.stream(
                method,
                self.target.api_url + path,
                json=body,
                headers={"Authorization": "Bearer " + self.token},
            ) as response:
                if missing and response.status_code == 404:
                    return None
                response.raise_for_status()
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 1_000_000:
                        raise ValueError("Kubernetes response too large")
        return json.loads(raw)

    def owned(self, item, run_id, commit, image=None):
        meta = (item or {}).get("metadata", {})
        labels = meta.get("labels", {})
        if (
            meta.get("namespace") != self.target.namespace
            or meta.get("name") != self.target.name(run_id)
            or labels.get("delta-run-id") != str(run_id)
            or labels.get("delta-commit") != commit
            or labels.get("app") != "delta-platform-sandbox"
        ):
            raise SandboxError(
                "sandbox_owner_changed", "온프레미스 자원 소유권이 달라 변경하지 않았습니다."
            )
        if image:
            spec = item["spec"]
            containers = spec.get("containers", [])
            if (
                len(containers) != 1
                or containers[0].get("image") != image
                or spec.get("automountServiceAccountToken") is not False
                or spec.get("serviceAccountName") != "runtime"
                or spec.get("nodeSelector", {}).get("kubernetes.io/hostname")
                != self.target.node_name
                or spec.get("hostNetwork", False)
                or spec.get("hostPID", False)
                or spec.get("hostIPC", False)
                or spec.get("initContainers")
                or spec.get("ephemeralContainers")
                or spec.get("activeDeadlineSeconds") != 900
                or spec.get("securityContext", {}).get("runAsNonRoot") is not True
                or spec.get("securityContext", {}).get("runAsUser") != 10001
                or spec.get("securityContext", {}).get("seccompProfile")
                != {"type": "RuntimeDefault"}
                or containers[0].get("securityContext", {}).get("allowPrivilegeEscalation")
                is not False
                or containers[0].get("securityContext", {}).get("readOnlyRootFilesystem")
                is not True
                or containers[0].get("securityContext", {}).get("privileged", False)
                or containers[0].get("securityContext", {}).get("capabilities", {}).get("drop")
                != ["ALL"]
                or containers[0].get("resources", {}).get("limits")
                != {"cpu": "500m", "memory": "256Mi", "ephemeral-storage": "256Mi"}
                or spec.get("volumes") != [{"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}}]
                or containers[0].get("volumeMounts") != [{"name": "tmp", "mountPath": "/tmp"}]
            ):
                raise SandboxError(
                    "sandbox_identity_mismatch", "온프레미스 이미지·격리 설정이 다릅니다."
                )

    def create(self, run_id, commit, image, port, guard):
        name = self.target.name(run_id)
        metadata = {
            "name": name,
            "namespace": self.target.namespace,
            "labels": {
                "app": "delta-platform-sandbox",
                "delta-run-id": str(run_id),
                "delta-commit": commit,
            },
        }
        guard()
        secret = self.call("GET", "secrets", name, missing=True)
        if secret:
            self.owned(secret, run_id, commit)
        else:
            if self.ecr is None:
                self.ecr = boto3.client(
                    "ecr",
                    region_name=self.target.registry_region,
                    config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2}),
                )
            auth = self.ecr.get_authorization_token()["authorizationData"][0]
            registry = image.split("/", 1)[0]
            if auth["proxyEndpoint"] != "https://" + registry:
                raise SandboxError("sandbox_registry_mismatch", "이미지 저장소 권한이 다릅니다.")
            config = json.dumps({"auths": {registry: {"auth": auth["authorizationToken"]}}})
            self.call(
                "POST",
                "secrets",
                body={
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": metadata,
                    "type": "kubernetes.io/dockerconfigjson",
                    "data": {".dockerconfigjson": base64.b64encode(config.encode()).decode()},
                },
            )
        guard()
        self.call(
            "POST",
            "pods",
            body={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": metadata,
                "spec": {
                    "serviceAccountName": "runtime",
                    "automountServiceAccountToken": False,
                    "restartPolicy": "Never",
                    "activeDeadlineSeconds": 900,
                    "terminationGracePeriodSeconds": 10,
                    "nodeSelector": {"kubernetes.io/hostname": self.target.node_name},
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 10001,
                        "runAsGroup": 10001,
                        "fsGroup": 10001,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "imagePullSecrets": [{"name": name}],
                    "containers": [
                        {
                            "name": "app",
                            "image": image,
                            "imagePullPolicy": "Always",
                            "ports": [{"name": "http", "containerPort": port}],
                            "env": [
                                {"name": "DELTA_RUN_ID", "value": str(run_id)},
                                {"name": "DELTA_COMMIT", "value": commit},
                            ],
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "resources": {
                                "requests": {
                                    "cpu": "100m",
                                    "memory": "128Mi",
                                    "ephemeral-storage": "64Mi",
                                },
                                "limits": {
                                    "cpu": "500m",
                                    "memory": "256Mi",
                                    "ephemeral-storage": "256Mi",
                                },
                            },
                            "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],
                        }
                    ],
                    "volumes": [{"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}}],
                },
            },
        )

    def verify(self, run_id, commit, image, path, guard):
        deadline = time.monotonic() + self.timeout
        while True:
            guard()
            pod = self.call("GET", "pods", self.target.name(run_id))
            self.owned(pod, run_id, commit, image)
            statuses = pod.get("status", {}).get("containerStatuses", [])
            if pod.get("status", {}).get("phase") in ("Failed", "Succeeded"):
                raise SandboxError(
                    "sandbox_execution_failed", "온프레미스 앱이 실행 중 종료됐습니다."
                )
            if len(statuses) == 1 and statuses[0].get("ready"):
                actual = statuses[0].get("imageID", "")
                if not actual.endswith("@" + image.rsplit("@", 1)[1]):
                    raise SandboxError(
                        "sandbox_image_mismatch", "실제 실행 이미지 digest가 다릅니다."
                    )
                break
            if time.monotonic() > deadline:
                raise SandboxError("sandbox_timeout", "온프레미스 앱 시작 시간이 초과됐습니다.")
            time.sleep(self.poll_seconds)
        svc = self.call("GET", "services", "sandbox")
        if (
            svc["spec"].get("selector") != {"app": "delta-platform-sandbox"}
            or len(svc["spec"].get("ports", [])) != 1
            or svc["spec"]["ports"][0].get("nodePort") != self.target.node_port
            or svc["spec"]["ports"][0].get("targetPort") != "http"
        ):
            raise SandboxError(
                "sandbox_identity_mismatch", "온프레미스 검사 서비스 연결이 다릅니다."
            )
        while True:
            guard()
            try:
                with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
                    with client.stream("GET", self.target.url + path) as response:
                        if 200 <= response.status_code < 400:
                            break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                raise SandboxError(
                    "sandbox_health_failed", "온프레미스 HTTP 응답을 확인하지 못했습니다."
                )
            time.sleep(self.poll_seconds)
        return {
            "resource": self.target.resource(run_id),
            "revision": pod["metadata"]["uid"],
            "url": self.target.url,
            "image_digest": image.rsplit("@", 1)[1],
        }

    def cleanup(self, run_id, commit, image, guard):
        name = self.target.name(run_id)
        for collection in ("pods", "secrets"):
            guard()
            item = self.call("GET", collection, name, missing=True)
            if item is None:
                continue
            self.owned(item, run_id, commit, image if collection == "pods" else None)
            self.call(
                "DELETE",
                collection,
                name,
                {
                    "apiVersion": "v1",
                    "kind": "DeleteOptions",
                    "preconditions": {"uid": item["metadata"]["uid"]},
                },
                missing=True,
            )
            deadline = time.monotonic() + 90
            while self.call("GET", collection, name, missing=True) is not None:
                guard()
                if time.monotonic() > deadline:
                    raise SandboxError(
                        "sandbox_cleanup_required", "온프레미스 자원 삭제 확인이 필요합니다."
                    )
                time.sleep(self.poll_seconds)


class OnpremSandboxRunner:
    def __init__(self, settings, *, provider=None, inspector=None):
        self.target = configured_target(settings)
        self.config = OnpremTarget.model_validate(self.target)
        self.provider = provider or K3s(
            self.config, settings.onprem_sandbox_token.get_secret_value()
        )
        self.inspector = inspector

    def execute(self, claim, store, progress):
        with store.sandbox_lock() as acquired:
            if not acquired:
                raise LeaseLost()
            return self._execute(claim, store, progress)

    def _execute(self, claim, store, progress):
        run_id, token, source = claim["id"], claim["lease_token"], claim["input_snapshot"]
        if source["sandbox_target"] != self.target:
            progress(
                "sandbox_cleanup_wait", "기존 온프레미스 연결 설정을 복원해야 정리할 수 있습니다."
            )
            raise LeaseLost()
        image, commit = source["image"], claim["resolved_commit_sha"]
        cleaning = False

        def guard():
            cancelled = store.sandbox_control(run_id, token)
            store.heartbeat(run_id, token)
            if cancelled and not cleaning:
                raise SandboxError("sandbox_cancelled", "온프레미스 실행 중단을 요청했습니다.")

        outcome = claim["submission"]
        try:
            if not outcome:
                guard()
                if claim["attempt"] > 3:
                    raise SandboxError(
                        "sandbox_recovery_exhausted", "온프레미스 복구 횟수를 초과했습니다."
                    )
                if (
                    not re.fullmatch(r"sha256:[a-f0-9]{64}", source["image_digest"])
                    or not image.endswith("@" + source["image_digest"])
                    or image.rsplit("@", 1)[0] + "@" not in self.config.allowed_image_prefixes
                ):
                    raise SandboxError(
                        "sandbox_image_invalid", "허용된 저장소의 고정 이미지만 실행합니다."
                    )
                resource = self.config.resource(run_id)
                if claim["external_id"] not in (None, resource):
                    raise SandboxError("sandbox_identity_mismatch", "저장된 실행 자원이 다릅니다.")
                if not claim["external_id"]:
                    store.attach_sandbox(run_id, token, resource)
                pod = self.provider.call("GET", "pods", self.config.name(run_id), missing=True)
                if pod is None:
                    progress(
                        "sandbox_deploy", "온프레미스의 격리된 공간에서 동일 이미지를 실행합니다."
                    )
                    self.provider.create(run_id, commit, image, source["container_port"], guard)
                progress("sandbox_verify", "온프레미스 실행 이미지와 HTTP 응답을 확인합니다.")
                checked = self.provider.verify(
                    run_id, commit, image, source["healthcheck_path"], guard
                )
                result = {
                    **checked,
                    "build_id": source["build_id"],
                    "image": image,
                    "commit_sha": commit,
                    "platform": "linux/amd64",
                    "runtime": "k3s",
                    "execution_cloud": "onprem",
                    "environment_check": "onprem_sandbox_only",
                    "runtime_check": "passed",
                    "security_check": "not_run",
                    "container_port": source["container_port"],
                    "healthcheck_path": source["healthcheck_path"],
                    "checked_at": datetime.now(UTC).isoformat(),
                }
                if self.inspector:
                    result.update(self.inspector.execute(claim, store, progress, guard, result))
                    self.provider.verify(run_id, commit, image, source["healthcheck_path"], guard)
                outcome = store.sandbox_checkpoint(run_id, token, {"result": result})
        except LeaseLost:
            raise
        except Exception as exc:
            error = (
                exc.payload
                if isinstance(exc, SandboxError)
                else {
                    "code": "sandbox_provider_error",
                    "message": "온프레미스 실행에 실패했습니다. 연결·권한·자원 정리를 확인하세요.",
                }
            )
            outcome = store.sandbox_checkpoint(run_id, token, {"error": error})
        cleaning = True
        try:
            stopped = self.inspector.stop(claim) if self.inspector else True
            progress("sandbox_cleanup", "이 실행의 온프레미스 앱과 이미지 인증 정보를 정리합니다.")
            self.provider.cleanup(run_id, commit, image, guard)
            if not stopped:
                raise ValueError("DAST still running")
        except LeaseLost:
            raise
        except Exception:
            progress(
                "sandbox_cleanup_wait", "온프레미스 정리를 확인하지 못해 실행 공간을 잠갔습니다."
            )
            raise LeaseLost() from None
        if store.sandbox_control(run_id, token):
            raise SandboxError("sandbox_cancelled", "온프레미스 앱을 중단하고 정리했습니다.")
        if "error" in outcome:
            raise SandboxError(**outcome["error"])
        progress("sandbox_complete", "온프레미스 실행과 정리를 확인했습니다.")
        return {
            **outcome["result"],
            "cleanup": "stopped",
            "completed_at": datetime.now(UTC).isoformat(),
        }
