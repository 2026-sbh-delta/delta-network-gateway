"""Delete only registered application UIDs; no VM, namespace, Secret or storage APIs."""

import base64
import ipaddress
import json
import ssl
import time
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from delta_platform.adapters.sandbox import SandboxError
from delta_platform.core.errors import Problem
from delta_platform.core.runtime import OnpremRuntime


class RemovalTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    cluster_id: UUID
    api_url: str
    ca_base64: str = Field(max_length=32768)
    namespaces: list[str] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def private_cluster(self):
        import re

        url = urlsplit(self.api_url)
        if (
            url.scheme != "https"
            or url.path
            or url.query
            or url.fragment
            or url.username
            or url.password
            or url.port != 6443
        ):
            raise ValueError("Registered private Kubernetes API required")
        ip = ipaddress.ip_address(url.hostname)
        if ip.version != 4 or not ip.is_private or ip.is_unspecified or ip.is_link_local:
            raise ValueError("Private cluster address required")
        ssl.create_default_context(cadata=base64.b64decode(self.ca_base64, validate=True).decode())
        if any(not re.fullmatch(r"delta-[a-z0-9-]{1,50}", n) for n in self.namespaces):
            raise ValueError("Dedicated platform namespaces required")
        return self


def configured_target(settings):
    if not settings.onprem_runtime_target or not settings.onprem_runtime_token.get_secret_value():
        raise Problem(
            "runtime_not_configured", "온프레미스 앱 삭제 실행기가 연결되지 않았습니다.", 503
        )
    try:
        return RemovalTarget.model_validate_json(settings.onprem_runtime_target).model_dump(
            mode="json"
        )
    except (ValueError, TypeError):
        raise Problem(
            "runtime_config_invalid", "온프레미스 앱 삭제 연결 설정을 확인하세요.", 503
        ) from None


class OnpremRemover:
    def __init__(self, settings, *, transport=None, timeout=60, poll_seconds=2):
        self.target = configured_target(settings)
        self.token = settings.onprem_runtime_token.get_secret_value()
        self.tls = ssl.create_default_context(
            cadata=base64.b64decode(self.target["ca_base64"]).decode()
        )
        self.transport, self.timeout, self.poll_seconds = transport, timeout, poll_seconds

    def call(self, method, namespace, resource, body=None):
        if resource.kind == "Deployment":
            path = f"/apis/apps/v1/namespaces/{namespace}/deployments/{resource.name}"
        else:
            path = f"/api/v1/namespaces/{namespace}/services/{resource.name}"
        with httpx.Client(
            verify=self.tls,
            transport=self.transport,
            timeout=10,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            with client.stream(
                method,
                self.target["api_url"] + path,
                json=body,
                headers={"Authorization": "Bearer " + self.token},
            ) as response:
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 1_000_000:
                        raise ValueError("Kubernetes response too large")
        return json.loads(raw)

    def owned(self, item, resource, namespace, service_id, environment_id):
        meta = item.get("metadata", {})
        labels = meta.get("labels", {})
        if (
            meta.get("namespace") != namespace
            or meta.get("name") != resource.name
            or meta.get("uid") != str(resource.uid)
            or labels.get("app.kubernetes.io/managed-by") != "delta-platform"
            or labels.get("delta-platform/service-id") != str(service_id)
            or labels.get("delta-platform/environment-id") != str(environment_id)
        ):
            raise SandboxError(
                "runtime_owner_changed",
                "삭제 대상의 소유권이 변경돼 중단했습니다. 운영자 확인이 필요합니다.",
            )
        if resource.kind == "Deployment":
            pod = item.get("spec", {}).get("template", {}).get("spec", {})
            if len(pod.get("containers", [])) != 1 or any(
                "persistentVolumeClaim" in volume or "ephemeral" in volume
                for volume in pod.get("volumes", [])
            ):
                raise SandboxError(
                    "runtime_storage_review_required",
                    "데이터 볼륨 또는 여러 컨테이너가 연결된 앱입니다. 운영자의 보존 범위 확인이 필요합니다.",
                )

    def execute(self, claim, store, progress):
        snapshot = claim["input_snapshot"]
        runtime = OnpremRuntime.model_validate(snapshot["runtime"])
        from delta_platform.core.store import digest

        if (
            digest({"runtime": snapshot["runtime"], "target": snapshot["target"]})
            != snapshot["binding_hash"]
        ):
            raise SandboxError("runtime_target_changed", "승인한 삭제 대상과 다릅니다.")
        if (
            snapshot["target"] != self.target
            or str(runtime.cluster_id) != self.target["cluster_id"]
            or runtime.namespace not in self.target["namespaces"]
        ):
            raise SandboxError(
                "runtime_target_changed", "등록한 온프레미스 연결이 변경돼 삭제하지 않았습니다."
            )

        def guard():
            store.heartbeat(claim["id"], claim["lease_token"])

        def read(resource):
            guard()
            item = self.call("GET", runtime.namespace, resource)
            if item:
                self.owned(
                    item,
                    resource,
                    runtime.namespace,
                    claim["service_id"],
                    snapshot["environment_id"],
                )
            return item

        try:
            progress("runtime_remove_check", "등록된 앱 자원의 소유권을 확인합니다.")
            # Validate every resource before performing the first destructive request.
            for resource in runtime.resources:
                read(resource)
            progress(
                "runtime_removing", "실행 앱과 전용 접속점을 제거합니다. DB·볼륨은 보존합니다."
            )
            for resource in sorted(runtime.resources, key=lambda r: (r.kind != "Service", r.name)):
                item = read(resource)
                if not item:
                    continue
                guard()
                self.call(
                    "DELETE",
                    runtime.namespace,
                    resource,
                    {
                        "apiVersion": "v1",
                        "kind": "DeleteOptions",
                        "propagationPolicy": "Foreground",
                        "preconditions": {
                            "uid": str(resource.uid),
                            "resourceVersion": item["metadata"]["resourceVersion"],
                        },
                    },
                )
            deadline = time.monotonic() + self.timeout
            while True:
                remaining = [r for r in runtime.resources if read(r) is not None]
                if not remaining:
                    break
                if time.monotonic() >= deadline:
                    raise SandboxError(
                        "runtime_cleanup_pending",
                        "앱 종료 확인이 지연됩니다. 상태를 확인한 뒤 다시 시도하세요.",
                    )
                time.sleep(self.poll_seconds)
        except (httpx.HTTPError, ValueError, KeyError):
            raise SandboxError(
                "runtime_connection_failed",
                "온프레미스 연결·권한을 확인하고 다시 시도하세요. 일부 앱이 이미 제거됐을 수 있습니다.",
            ) from None
        progress("runtime_removed", "실행 앱 삭제를 확인했습니다. DB·볼륨·서버는 보존했습니다.")
        return {
            "environment_id": snapshot["environment_id"],
            "runtime_status": "removed",
            "preserved": ["database", "persistent_volumes", "host", "cluster"],
            "resources": [r.model_dump(mode="json") for r in runtime.resources],
        }
