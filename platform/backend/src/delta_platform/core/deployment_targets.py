"""Small operator-controlled deployment catalog, shared by registration and execution."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .errors import Problem


class DeploymentTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    cloud: Literal["aws", "gcp", "onprem"]
    region: str = Field(max_length=64)
    profile_id: str = Field(max_length=64)
    host_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def supported(self):
        option = OPTIONS[self.cloud]
        if self.region != option["region"] or self.profile_id not in {
            p["id"] for p in option["profiles"]
        }:
            raise ValueError("지원하는 리전과 사양을 선택하세요.")
        if self.host_id != ("delta-onprem" if self.cloud == "onprem" else None):
            raise ValueError("등록된 온프레미스 호스트를 선택하세요.")
        return self


OPTIONS = {
    "aws": {
        "cloud": "aws",
        "label": "AWS",
        "runtime": "VM",
        "region": "ap-northeast-2",
        "profiles": [
            {
                "id": "t3.micro",
                "label": "t3.micro",
                "cpu": "2 vCPU (버스트)",
                "memory_mb": 1024,
                "description": "작은 단일 앱",
            },
            {
                "id": "t3.small",
                "label": "t3.small",
                "cpu": "2 vCPU (버스트)",
                "memory_mb": 2048,
                "description": "FE·BE 함께 실행",
            },
        ],
        "hosts": [],
    },
    "gcp": {
        "cloud": "gcp",
        "label": "GCP",
        "runtime": "VM",
        "region": "asia-northeast3",
        "profiles": [
            {
                "id": "e2-micro",
                "label": "e2-micro",
                "cpu": "공유 코어",
                "memory_mb": 1024,
                "description": "작은 단일 앱",
            },
            {
                "id": "e2-small",
                "label": "e2-small",
                "cpu": "공유 코어",
                "memory_mb": 2048,
                "description": "FE·BE 함께 실행",
            },
        ],
        "hosts": [],
    },
    "onprem": {
        "cloud": "onprem",
        "label": "온프레미스",
        "runtime": "K3s",
        "region": "onprem-local",
        "profiles": [
            {
                "id": "small",
                "label": "작게",
                "cpu": "500m",
                "memory_mb": 512,
                "description": "앱 전체 최대 자원",
            },
            {
                "id": "medium",
                "label": "보통",
                "cpu": "1000m",
                "memory_mb": 1024,
                "description": "앱 전체 최대 자원",
            },
        ],
        "hosts": [{"id": "delta-onprem", "label": "팀 온프레미스 서버"}],
    },
}


class Connection(BaseModel):
    """Public identity only. Credentials/network settings remain on the deployment agent."""

    model_config = ConfigDict(extra="forbid")
    revision: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    account_id: str | None = Field(default=None, pattern=r"^[0-9]{12}$")
    gcp_project: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
    host_id: str | None = None


def connection(settings, cloud):
    try:
        raw = json.loads(settings.deployment_connections or "{}")
        value = Connection.model_validate(raw[cloud])
        if cloud == "aws" and (not value.account_id or value.gcp_project or value.host_id):
            raise ValueError()
        if cloud == "gcp" and (not value.gcp_project or value.account_id or value.host_id):
            raise ValueError()
        if cloud == "onprem" and (
            value.host_id != "delta-onprem" or value.account_id or value.gcp_project
        ):
            raise ValueError()
        return value.model_dump(exclude_none=True)
    except (ValueError, KeyError, TypeError):
        raise Problem(
            "deployment_not_configured", "운영자의 배포 연결 설정이 필요합니다.", 503
        ) from None


def catalog(settings):
    from delta_platform.adapters.deployment import deployment_settings
    from delta_platform.adapters.jenkins import jenkins_target

    items = []
    for cloud, option in OPTIONS.items():
        reason = None
        try:
            connection(settings, cloud)
            jenkins_target(deployment_settings(settings))
        except Problem as exc:
            reason = exc.message
        items.append({**option, "configured": reason is None, "reason": reason})
    return {"items": items}
