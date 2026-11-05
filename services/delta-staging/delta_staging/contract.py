"""스테이징 도구 호출 계약. 관리 에이전트는 이 스키마만 알면 되고 클라우드별 구현은 몰라도 된다."""

from __future__ import annotations

import ipaddress
import hashlib
import json
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Cloud = Literal["aws", "gcp"]
State = Literal["deploying", "ready", "failed", "stopped", "replaced"]

IMAGE_PATTERN = r"^[a-z0-9][a-z0-9.-]*(:[0-9]{1,5})?(/[a-z0-9._-]+)+(:[A-Za-z0-9_.-]{1,128})?(@sha256:[0-9a-f]{64})?$"
ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
SECRET_KEY = re.compile(r"SECRET|PASSWORD|PASSWD|TOKEN|PRIVATE|API_KEY|ACCESS_KEY|CREDENTIAL")
MAX_ENV = 50
MAX_ENV_VALUE = 4096


def bundle_hash(commit: str, image_digests: dict[str, str]) -> str:
    content = {"schema": 2, "commit": commit, "platform": "linux/amd64", "image_digests": image_digests}
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DeployRequest(_Model):
    """스테이징 배포 요청. 같은 run_id 로 다시 부르면 새로 띄우지 않고 기존 상태를 돌려준다."""

    run_id: str = Field(pattern=r"^[A-Za-z0-9._-]{1,64}$", description="파이프라인 실행 ID")
    cloud: Cloud = Field(default="aws", description="스테이징을 띄울 클라우드")
    image: str = Field(pattern=IMAGE_PATTERN, max_length=512,
                       description="빌드된 컨테이너 이미지. 재현을 위해 @sha256 digest 를 권장한다")
    commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{7,40}$", description="이미지를 만든 커밋 SHA")
    images: dict[str, str] | None = None
    image_digests: dict[str, str] | None = None
    bundle_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    env: dict[str, str] = Field(default_factory=dict, description="비밀이 아닌 앱 환경 변수")
    replace: bool = Field(default=False, description="다른 실행이 스테이징을 쓰고 있으면 교체한다")
    lease_holder: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9._-]{1,64}$",
        description="스테이징(과 그 DB)을 점유할 실행 ID. 비우면 run_id. 점유 중에는 다른 실행이 배포·초기화하지 못한다")
    lease_seconds: int = Field(default=3600, ge=60, le=14400,
                               description="점유 유지 시간. 점검이 끝나면 점유한 실행이 풀고, 못 풀면 이 시간 뒤 풀린다")
    scanner_ip: str | None = Field(
        default=None,
        description="공개 스테이징에 접근할 DAST 실행기의 공인 IPv4. auto 면 도구를 실행하는 곳의 현재 공인 IP 를 확인해 쓴다")

    @model_validator(mode="after")
    def _bundle(self) -> DeployRequest:
        if not ({"images", "image_digests", "bundle_sha256"} & self.model_fields_set):
            return self
        if (not self.images or not self.image_digests or not self.bundle_sha256
                or set(self.images) != {"frontend", "backend"}
                or set(self.image_digests) != {"frontend", "backend"}):
            raise ValueError("v2는 FE·BE 이미지, digest, 묶음 해시가 모두 필요하다")
        if not self.commit or len(self.commit) != 40:
            raise ValueError("v2는 full40 commit이 필요하다")
        if set(self.env) & {"PGHOST", "PGPORT", "PGUSER", "PGDATABASE", "PGSSLMODE", "PGSSLROOTCERT",
                            "DATABASE_URL", "APP_RESET_DATABASE"}:
            raise ValueError("DB 연결·reset 대상은 서버 설정으로만 제공한다")
        for name, image in self.images.items():
            digest = self.image_digests[name]
            if (len(image) > 512 or not re.fullmatch(IMAGE_PATTERN, image)
                    or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
                    or not image.endswith("@" + digest)):
                raise ValueError("v2 이미지와 고정 digest가 일치해야 한다")
        if self.image != self.images["frontend"]:
            raise ValueError("image는 frontend 별칭이어야 한다")
        if self.bundle_sha256 != bundle_hash(self.commit, self.image_digests):
            raise ValueError("이미지 묶음 해시가 일치하지 않는다")
        return self

    @field_validator("scanner_ip")
    @classmethod
    def _public_ipv4(cls, value: str | None) -> str | None:
        if value is None or value == "auto":
            return value
        address = ipaddress.ip_address(value)
        if address.version != 4 or not address.is_global:
            raise ValueError("scanner_ip 는 공인 IPv4 이거나 auto 다")
        return value

    @field_validator("env")
    @classmethod
    def _plain_env_only(cls, env: dict[str, str]) -> dict[str, str]:
        if len(env) > MAX_ENV:
            raise ValueError(f"env 는 {MAX_ENV}개까지다")
        for key, value in env.items():
            if not ENV_KEY.match(key):
                raise ValueError(f"env 이름이 잘못됐다: {key}")
            if SECRET_KEY.search(key):
                raise ValueError(f"비밀 값은 env 로 넘기지 않는다: {key}")
            if len(value) > MAX_ENV_VALUE:
                raise ValueError(f"env 값은 {MAX_ENV_VALUE}자까지다: {key}")
        return env

    @property
    def pinned(self) -> bool:
        return "@sha256:" in self.image


class Lease(_Model):
    """클라우드별 스테이징 점유. 진행 중인 DAST 가 쓰는 DB 를 다른 실행이 초기화하지 못하게 한다."""

    cloud: Cloud
    holder: str
    deployment: str = Field(description="점유를 잡은 배포 run_id")
    acquired_at: datetime
    expires_at: datetime


class LeaseRelease(_Model):
    holder: str = Field(pattern=r"^[A-Za-z0-9._-]{1,64}$")


class Step(_Model):
    name: str
    at: datetime
    elapsed_seconds: float = Field(default=0, description="배포 요청을 받은 뒤 이 단계까지 걸린 시간")
    detail: str = ""


class Deployment(_Model):
    run_id: str
    cloud: Cloud
    state: State
    image: str
    pinned: bool
    commit: str | None = None
    images: dict[str, str] = Field(default_factory=dict)
    image_digests: dict[str, str] = Field(default_factory=dict)
    bundle_sha256: str | None = None
    data_reset_remaining: int | None = Field(default=None, strict=True, ge=0)
    databaseVersionNum: int | None = Field(default=None, strict=True)
    database_version_num: int | None = Field(default=None, strict=True)
    request_fingerprint: str | None = None
    target_fingerprint: str | None = None
    handoff_required: tuple[str, ...] = ()
    s06_verified: bool = False
    pre_dast_remaining: int | None = Field(default=None, strict=True, ge=0)
    url: str | None = Field(default=None, description="스캐너만 닿는 내부 주소. ready 일 때만 채운다")
    revision: str | None = Field(default=None, description="클라우드의 배포 개정판 식별자")
    image_digest: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$",
        description="실행 중인 태스크에서 확인한 이미지 digest(요청 값이 아니다). 빌드 기록과 대조하는 빌드 식별자")
    task_arn: str | None = Field(default=None, description="digest를 확인한 실행 중 태스크")
    scanner_ip: str | None = Field(default=None, description="이번 실행에 허용한 스캐너 IP")
    started_at: datetime
    ready_at: datetime | None = None
    ended_at: datetime | None = None
    error: str | None = None
    steps: tuple[Step, ...] = ()
