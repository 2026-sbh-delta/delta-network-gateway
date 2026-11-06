"""Analysis-only contract shared with the graph. It contains no workflow commands or approval authority."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from delta_ai.secrets_pattern import MASK, redact

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
Text = Annotated[str, Field(min_length=1, max_length=4000)]


def safe_text(text: str) -> str:
    text = re.sub(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        MASK,
        text,
        flags=re.DOTALL,
    )
    text = re.sub(
        r"""["']?(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|authorization)["']?\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;]+)""",
        MASK,
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\bBearer\s+[^\s,;]+", MASK, text, flags=re.IGNORECASE)
    return redact(text)


def scrub(value):
    if isinstance(value, str):
        return safe_text(value)
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    return value


class SafeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="before")
    @classmethod
    def scrub_text(cls, value):
        return scrub(value)


class Observation(SafeModel):
    evidence_id: Identifier
    source: Identifier
    summary: Text
    rule_ids: tuple[Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9-]{0,63}$")], ...] = (
        Field(default=(), max_length=20)
    )


class DiagnosisRequest(SafeModel):
    case_id: Identifier
    run_id: Identifier
    service_id: Identifier
    node_id: Identifier
    trigger: Literal["ambiguous_result", "execution_failure"]
    observations: tuple[Observation, ...] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def unique_evidence(self):
        ids = [o.evidence_id for o in self.observations]
        if len(ids) != len(set(ids)):
            raise ValueError("진단 근거 ID가 중복됐다")
        return self


def diagnosis_hash(request: DiagnosisRequest) -> str:
    return hashlib.sha256(
        json.dumps(
            request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


class Hypothesis(SafeModel):
    explanation: Text
    confidence: Literal["low", "medium", "high"]
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=40)
    rule_ids: tuple[str, ...] = Field(default=(), max_length=20)


class DiagnosisDraft(SafeModel):
    summary: Text
    hypotheses: tuple[Hypothesis, ...] = Field(default=(), max_length=10)
    next_checks: tuple[Text, ...] = Field(min_length=1, max_length=20)
    limitations: tuple[Text, ...] = Field(min_length=1, max_length=20)


class DiagnosisReport(DiagnosisDraft):
    case_id: Identifier
    run_id: Identifier
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["analyzed", "analysis_unavailable", "invalid_analysis"]
    model: str | None = Field(default=None, max_length=128)
    # This is always human review. There is deliberately no "next node", retry or approve field.
    requires_human: Literal[True] = True


def unavailable_report(
    request: DiagnosisRequest, *, status="analysis_unavailable"
) -> DiagnosisReport:
    return DiagnosisReport(
        case_id=request.case_id,
        run_id=request.run_id,
        input_hash=diagnosis_hash(request),
        status=status,
        summary="자동 분석을 완료하지 못했다. 관측 사실과 기존 안전 절차를 사람이 확인한다.",
        next_checks=("저장된 관측과 원래 검사 결과를 검토한다.",),
        limitations=(
            "이 보고서는 원인을 확정하거나 배포·재시도·승인을 허용하지 않는다.",
        ),
    )


def validate_report(
    report: DiagnosisReport,
    request: DiagnosisRequest,
    allowed_rules: set[str] | None = None,
):
    if (report.case_id, report.run_id, report.input_hash) != (
        request.case_id,
        request.run_id,
        diagnosis_hash(request),
    ):
        raise ValueError("진단 응답의 실행·입력 식별값이 다르다")
    evidence_ids = {o.evidence_id for o in request.observations}
    requested_rules = {r for o in request.observations for r in o.rule_ids}
    rules = requested_rules if allowed_rules is None else allowed_rules
    if any(
        not set(h.evidence_ids) <= evidence_ids or not set(h.rule_ids) <= rules
        for h in report.hypotheses
    ):
        raise ValueError("진단이 제공되지 않은 근거를 인용했다")
    return report
