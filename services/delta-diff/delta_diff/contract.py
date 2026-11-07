"""차분 요청·결과. 판정 본문은 delta-ai 의 AssessmentResult 를 그대로 싣는다."""

from __future__ import annotations

from typing import Literal

from delta_ai.contract import AssessmentResult, MappingInput
from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_EXCLUDES = 20
MAX_GAPS = 50


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StateRef(_Model):
    bucket: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
    key: str = Field(min_length=1, max_length=1024)


class DiffSource(_Model):
    provider: Literal["aws", "gcp", "onprem"] = "aws"
    state: StateRef | None = None
    manifests: str | None = Field(default=None, max_length=60_000)
    region: str | None = None
    exclude_subnets: tuple[str, ...] = Field(default=(), max_length=MAX_EXCLUDES)

    @model_validator(mode="after")
    def validate_source(self):
        if self.provider == "onprem":
            if self.state is not None or not self.manifests or self.region is not None:
                raise ValueError("onprem 출발에는 manifests만 필요하다")
        elif self.state is None or self.manifests is not None:
            raise ValueError("클라우드 출발에는 S3 state가 필요하다")
        return self


class DiffTarget(_Model):
    provider: Literal["aws", "gcp", "onprem"] = "gcp"
    manifests: str | None = Field(default=None, max_length=60_000)
    region: str | None = None

    @model_validator(mode="after")
    def validate_target(self):
        if self.manifests is not None and self.provider != "onprem":
            raise ValueError("대상 manifests는 onprem에서만 쓴다")
        if self.region is not None and self.provider == "onprem":
            raise ValueError("onprem은 클라우드 region을 쓰지 않는다")
        return self


class DiffRequest(_Model):
    diff_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    service_id: str = Field(min_length=1, max_length=128)
    source: DiffSource
    target: DiffTarget = DiffTarget()
    accepted_gaps: tuple[str, ...] = Field(default=(), max_length=MAX_GAPS)
    scc_tier: Literal["standard", "premium"] | None = None


class ManifestMeta(_Model):
    kind: Literal["kubernetes_yaml"] = "kubernetes_yaml"
    sha256: str
    resource_count: int


class SourceMeta(_Model):
    """어떤 state 를 읽었는지. 같은 serial 이면 같은 입력이다."""

    bucket: str
    key: str
    version_id: str | None = None
    serial: int | None = None
    lineage: str | None = None
    terraform_version: str | None = None
    resource_count: int
    excluded_capabilities: tuple[str, ...] = ()


class DiffResult(_Model):
    diff_id: str
    source: SourceMeta | ManifestMeta
    target_manifest: ManifestMeta | None = None
    mapping_input: MappingInput
    assessment: AssessmentResult
