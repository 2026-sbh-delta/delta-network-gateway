"""대상 설정. terraform/staging-aws, terraform/staging-gcp 의 tool_config 출력을 그대로 쓴다."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class ConfigError(Exception):
    pass


class AwsTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cloud: Literal["aws"] = "aws"
    region: str
    account_id: str = Field(pattern=r"^[0-9]{12}$")
    cluster: str
    service: str
    task_family: str
    container_name: str
    target_group_arn: str
    alb_security_group_id: str | None = None
    dynamic_scanner_access: bool = False  # 공개 ALB 에 실행기의 현재 IP 를 도구가 직접 허용한다
    url: str | None = None  # 없으면 대상 그룹이 붙은 ALB 에서 조회한다
    allowed_image_prefixes: tuple[str, ...] = Field(min_length=1)


class GcpTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cloud: Literal["gcp"] = "gcp"
    project_id: str = Field(pattern=r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
    region: str
    service: str
    container_name: str
    security_policy: str | None = None  # 외부 LB 앞의 Cloud Armor 정책
    dynamic_scanner_access: bool = False  # Cloud Armor 에 실행기의 현재 IP 를 도구가 직접 허용한다
    scanner_rule_priority: int = Field(default=1000, ge=0, lt=2147483647)
    # 외부 LB 의 전역 IP 이름. 주소는 쓸 때마다 이 이름으로 조회해 LB 를 다시 만들어도 설정을 고치지 않는다.
    address: str | None = None
    url: str | None = None  # 고정 주소를 굳이 줄 때만
    health_check_path: str = Field(default="/", pattern=r"^/")
    allowed_image_prefixes: tuple[str, ...] = Field(min_length=1)
    # 배포 직전에 같은 이미지로 돌리는 데이터 초기화 Cloud Run Job. 성공(남은 행 0)해야 새 개정판을 띄운다.
    reset_job: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{0,62}$")

    @model_validator(mode="after")
    def _policy_for_dynamic_access(self) -> GcpTarget:
        if self.dynamic_scanner_access and not self.security_policy:
            raise ValueError("dynamic_scanner_access 에는 security_policy 가 필요하다")
        if not (self.address or self.url):
            raise ValueError("address(전역 IP 이름) 또는 url 이 필요하다")
        return self


Target = Annotated[AwsTarget | GcpTarget, Field(discriminator="cloud")]
_TARGET = TypeAdapter(Target)


def load_targets(path: Path | str) -> dict[str, AwsTarget | GcpTarget]:
    """객체 하나(tool_config) 또는 그 목록을 읽어 클라우드별로 묶는다."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"스테이징 설정 파일이 없다: {path}. terraform output -json tool_config 결과를 넣는다") from exc
    entries = raw if isinstance(raw, list) else [raw]
    targets = {}
    for entry in entries:
        target = _TARGET.validate_python({"cloud": "aws", **entry})
        if target.cloud in targets:
            raise ValueError(f"같은 클라우드 대상이 두 번 있다: {target.cloud}")
        targets[target.cloud] = target
    return targets
