"""SAST 입력은 넓게 받고 실제 실행 선택은 registry에서 다시 제한한다."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ROOT = Path(__file__).resolve().parent.parent
MAX_REPORT_BYTES = 128 * 1024
SCENARIOS = [
    {"id": "vulnerable", "label": "취약한 게시판"},
    {"id": "fixed", "label": "출력 이스케이프 적용"},
    {"id": "csp", "label": "인라인 스크립트 CSP 차단"},
]
Scenario = Literal["vulnerable", "fixed", "csp"]
ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TriggerHint(StrictModel):
    target_id: str | None = Field(default=None, pattern=ID_PATTERN)
    route_id: str | None = Field(default=None, pattern=ID_PATTERN)
    field: str | None = Field(default=None, pattern=ID_PATTERN)
    payload_id: str | None = Field(default=None, pattern=ID_PATTERN)


class Finding(StrictModel):
    id: str = Field(pattern=ID_PATTERN)
    title: str = Field(min_length=1, max_length=200)
    cwe: str = Field(min_length=1, max_length=120)
    severity: str = Field(min_length=1, max_length=30)
    description: str = Field(min_length=1, max_length=4000)
    source_file: str = Field(min_length=1, max_length=300)
    line: int = Field(ge=1, le=10_000_000)
    trigger: TriggerHint | None = None
    payload: str | None = Field(default=None, max_length=8192)


class Report(StrictModel):
    source: str = Field(pattern=ID_PATTERN)
    source_label: str = Field(min_length=1, max_length=200)
    findings: list[Finding] = Field(min_length=1, max_length=10)

    @field_validator("findings")
    @classmethod
    def unique_ids(cls, value: list[Finding]) -> list[Finding]:
        if len({item.id for item in value}) != len(value):
            raise ValueError("finding ID는 중복될 수 없습니다.")
        return value


class RunRequest(StrictModel):
    scenario: Scenario
    planner: Literal["agent", "computer_use", "reviewed", "claude"]
    report: dict
    # AI 에이전트 모드에서 동시에 점검할 취약점 수. 취약점마다 별도 브라우저·에이전트를 쓴다.
    concurrency: int = Field(default=2, ge=1, le=5)
    # 취약이 확정되면(또는 HTML 본문 반사가 확인되면) 영향 시연 페이로드를 실행할지 여부.
    impact: bool = True
    # 점검을 묶을 프로젝트(시스템). 비우면 기본 프로젝트로 기록한다.
    project_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    # 취약 항목의 조치 권장 코드(수정안)를 점검 직후 자동으로 만든다. PR은 항상 사용자 승인 후 생성한다.
    auto_fix: bool = True


class Decision(StrictModel):
    finding_id: str = Field(pattern=ID_PATTERN)
    decision: Literal["verify", "insufficient"]
    target_id: str = Field(pattern=ID_PATTERN)
    route_id: str = Field(pattern=ID_PATTERN)
    field: str = Field(pattern=ID_PATTERN)
    payload_id: str = Field(pattern=ID_PATTERN)
    reason: str = Field(min_length=1, max_length=1000)


class Plan(StrictModel):
    decisions: list[Decision] = Field(min_length=1, max_length=10)
    model_name: str = Field(min_length=1, max_length=120)
    duration_ms: int = Field(ge=0)
    cost_usd: float | None = Field(default=None, ge=0)


def sample_report() -> dict:
    return json.loads((ROOT / "examples" / "sast-stored-xss.json").read_text())
