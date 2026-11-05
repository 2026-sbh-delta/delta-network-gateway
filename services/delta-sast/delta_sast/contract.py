"""SAST 도구 호출 계약. 관리 에이전트는 이 스키마만 알면 된다."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Mode = Literal["fast", "full"]
PayloadMode = Literal["ai", "preset"]
State = Literal["running", "done", "failed"]
DastStatus = Literal["REPRODUCED", "NOT_REPRODUCED", "ERROR"]

RUN_ID = r"^[A-Za-z0-9._-]{1,64}$"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ScanRequest(_Model):
    """점검 요청. 같은 run_id로 다시 부르면 새로 점검하지 않고 기존 상태를 돌려준다."""

    run_id: str = Field(pattern=RUN_ID, description="파이프라인 실행 ID")
    repo: str = Field(min_length=1, max_length=512,
                      description="점검할 저장소. 허용된 https git 주소 또는 허용된 로컬 경로")
    commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{7,40}$",
                               description="점검할 커밋 SHA. 원격 저장소는 필수")
    subdir: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._/-]{1,200}$",
                               description="저장소 안에서 점검할 하위 폴더(예: app)")
    mode: Mode = Field(default="fast", description="fast: 파이프라인용 빠른 점검. full: 전체 LLM 검토(준비 중)")
    payload_mode: PayloadMode = Field(default="ai", description="ai: LLM이 페이로드 틀을 고른다. preset: DAST 기본 틀")
    baseline_run_id: str | None = Field(
        default=None, pattern=RUN_ID,
        description="비교 기준 점검(예: 패치 전 회차). 주면 결과마다 baselineState를 달고 요약에 새 탐지 수를 준다")


class Step(_Model):
    name: str
    at: datetime
    detail: str = ""


class Scope(_Model):
    """점검 범위 증거. 정상적인 0건과 잘못된 범위(다른 커밋, 빈 폴더, 기대 밖 언어)를 가른다."""
    commit: str | None = Field(default=None, description="실제로 점검한 커밋(원격은 받아 온 HEAD, 로컬은 요청 커밋)")
    files: int = Field(description="점검 대상 소스 파일 수(제외 목록 밖)")
    languages: dict[str, int] = Field(default_factory=dict, description="언어 → 파일 수")


class Summary(_Model):
    scope: Scope | None = Field(default=None, description="점검 범위 증거")
    counts: dict[str, int] = Field(default_factory=dict, description="판정 주체:판정 → 개수, secrets → 비밀값 수")
    dast_targets: int = Field(default=0, description="DAST가 공격할 결과 수. 0이면 DAST를 건너뛸 수 있다")
    llm: dict[str, int] = Field(default_factory=dict, description="LLM 호출 수·캐시 적중 수·실패 수")
    timings: dict[str, float] = Field(default_factory=dict, description="단계별 초")
    baseline: "BaselineSummary | None" = Field(default=None, description="baseline_run_id를 줬을 때만")


class BaselineSummary(_Model):
    run_id: str
    new_findings: int = Field(description="기준에 없던 fail·review 결과 수. 0이 아니면 패치 통과 불가")
    new_review: int = Field(description="new_findings 중 review(판정 불확실) 수")
    still_open: int = Field(description="기준의 fail·review 중 여전히 fail·review인 수")
    resolved: int = Field(description="기준의 fail·review 중 pass가 됐거나 사라진 수")
    resolved_fingerprints: list[str] = Field(default_factory=list)
    new_fingerprints: list[str] = Field(default_factory=list)


class Scan(_Model):
    run_id: str
    state: State
    repo: str
    commit: str | None = None
    subdir: str | None = None
    baseline_run_id: str | None = None
    mode: Mode
    payload_mode: PayloadMode
    started_at: datetime
    ended_at: datetime | None = None
    error: str | None = None
    summary: Summary | None = None
    steps: tuple[Step, ...] = ()


class DastResult(_Model):
    fingerprint: str = Field(pattern=r"^[0-9a-f]{32}$", description='SARIF partialFingerprints["deltaFlow/v1"]')
    status: DastStatus


class DastFeedback(_Model):
    """DAST 재현 결과. LLM 판정과 맞으면 규칙 캐시로 굳힌다."""

    results: list[DastResult] = Field(min_length=1, max_length=500)


class FeedbackOutcome(_Model):
    fingerprint: str
    outcome: Literal["ineffective", "effective", "conflict", "skipped", "unknown"]
    detail: str = ""


Summary.model_rebuild()
