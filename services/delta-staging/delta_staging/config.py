"""대상 설정. terraform/staging-aws 의 tool_config 출력을 그대로 쓴다."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from delta_staging.contract import Cloud


class ConfigError(Exception):
    pass


class AwsTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cloud: Cloud = "aws"
    region: str
    account_id: str = Field(pattern=r"^[0-9]{12}$")
    cluster: str
    service: str
    task_family: str
    container_name: str
    target_group_arn: str
    alb_security_group_id: str | None = None
    dynamic_scanner_access: bool = False  # 공개 ALB 에 실행기의 현재 IP 를 도구가 직접 허용한다
    url: str
    allowed_image_prefixes: tuple[str, ...] = Field(min_length=1)


def load_targets(path: Path | str) -> dict[str, AwsTarget]:
    """객체 하나(tool_config) 또는 그 목록을 읽어 클라우드별로 묶는다."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"스테이징 설정 파일이 없다: {path}. terraform output -json tool_config 결과를 넣는다") from exc
    entries = raw if isinstance(raw, list) else [raw]
    targets = {}
    for entry in entries:
        target = AwsTarget.model_validate(entry)
        if target.cloud in targets:
            raise ValueError(f"같은 클라우드 대상이 두 번 있다: {target.cloud}")
        targets[target.cloud] = target
    return targets
