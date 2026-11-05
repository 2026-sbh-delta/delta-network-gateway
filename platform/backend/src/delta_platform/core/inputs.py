import re
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def clean_path(value, allow_root=False):
    if (
        not value
        or len(value) > 256
        or value.startswith("/")
        or "\\" in value
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
        or ".." in value.split("/")
    ):
        raise ValueError("저장소 내부의 상대 경로를 입력하세요.")
    result = str(PurePosixPath(value))
    if (result == "." and not allow_root) or len(PurePosixPath(result).parts) > 8:
        raise ValueError("경로가 비어 있거나 너무 깊습니다.")
    return result


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class LoginInput(StrictInput):
    login_id: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024, repr=False)


class EmptyInput(StrictInput):
    pass


class DeleteServiceInput(StrictInput):
    confirmation_name: str = Field(min_length=1, max_length=80)


class RemoveRuntimeInput(DeleteServiceInput):
    binding_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class BuildInput(StrictInput):
    source_check_id: str = Field(min_length=36, max_length=36)

    @field_validator("source_check_id")
    @classmethod
    def valid_id(cls, value):
        return str(UUID(value))


class ImageCheckInput(StrictInput):
    build_id: str = Field(min_length=36, max_length=36)
    execution_cloud: Literal["aws", "gcp", "onprem"] = "aws"

    @field_validator("build_id")
    @classmethod
    def valid_id(cls, value):
        return str(UUID(value))


class SandboxCheckInput(ImageCheckInput):
    backend_build_id: str | None = None

    @field_validator("backend_build_id")
    @classmethod
    def valid_backend_id(cls, value):
        return str(UUID(value)) if value else None

    @model_validator(mode="after")
    def bundle_target(self):
        if self.backend_build_id and self.execution_cloud != "onprem":
            raise ValueError("FE·BE 배포 묶음은 현재 온프레미스에서 지원합니다.")
        if self.backend_build_id == self.build_id:
            raise ValueError("FE와 BE에는 각각의 빌드가 필요합니다.")
        return self


class DastCheckInput(SandboxCheckInput):
    security_check_id: str = Field(min_length=36, max_length=36)

    @field_validator("security_check_id")
    @classmethod
    def valid_security_id(cls, value):
        return str(UUID(value))


class ServiceInput(StrictInput):
    name: str = Field(min_length=1, max_length=80)
    repository_url: str = Field(max_length=512)
    source_ref: str = Field(min_length=1, max_length=255)
    build_context: str = "."
    dockerfile_path: str = "Dockerfile"
    container_port: int = Field(ge=1, le=65535)
    healthcheck_path: str = Field(min_length=1, max_length=256)

    @field_validator("repository_url")
    @classmethod
    def repository(cls, value):
        match = re.fullmatch(
            r"https://github\.com/([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9_.-]{1,100})", value
        )
        if not match:
            raise ValueError("공개 GitHub 저장소 주소를 입력하세요.")
        owner, repo = match.groups()
        repo = repo.removesuffix(".git")
        if repo in ("", ".", ".."):
            raise ValueError("저장소 이름이 올바르지 않습니다.")
        return f"https://github.com/{owner.lower()}/{repo.lower()}"

    @field_validator("name", "source_ref")
    @classmethod
    def text(cls, value):
        if not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("빈 값이나 제어문자를 사용할 수 없습니다.")
        return value

    @field_validator("build_context")
    @classmethod
    def context(cls, value):
        return clean_path(value, allow_root=True)

    @field_validator("dockerfile_path")
    @classmethod
    def dockerfile(cls, value):
        return clean_path(value)

    @field_validator("healthcheck_path")
    @classmethod
    def health_path(cls, value):
        if (
            not value.startswith("/")
            or value.startswith("//")
            or any(c in value for c in "?#\\")
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
        ):
            raise ValueError("/로 시작하는 앱 내부 경로를 입력하세요.")
        return value

    @model_validator(mode="after")
    def within_context(self):
        if not PurePosixPath(self.dockerfile_path).is_relative_to(
            PurePosixPath(self.build_context)
        ):
            raise ValueError("Dockerfile은 빌드 경로 안에 있어야 합니다.")
        return self


def request_key(value):
    if not value or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value):
        from .errors import Problem

        raise Problem("invalid_idempotency_key", "유효한 Idempotency-Key가 필요합니다.", 422)
    return value
