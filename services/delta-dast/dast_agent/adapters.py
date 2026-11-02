"""외부 SAST 결과를 좁은 내부 finding 형식으로 변환한다."""

from pydantic import BaseModel, ConfigDict, Field


class AWSModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class AWSCodeLocation(AWSModel):
    filePath: str = Field(default="unknown", max_length=300)
    lineStart: int = Field(default=1, ge=1, le=10_000_000)
    lineEnd: int | None = Field(default=None, ge=1, le=10_000_000)
    label: str | None = Field(default=None, max_length=200)


class AWSFinding(AWSModel):
    findingId: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=3000)
    reasoning: str = Field(default="", max_length=3000)
    riskType: str = Field(default="UNKNOWN", max_length=120)
    riskLevel: str = Field(default="INFO", max_length=30)
    codeLocations: list[AWSCodeLocation] = Field(default_factory=list, max_length=50)


class AWSReport(AWSModel):
    source: str
    source_label: str = Field(min_length=1, max_length=200)
    findings: list[AWSFinding] = Field(min_length=1, max_length=10)


def _cwe(risk_type: str) -> str:
    normalized = risk_type.upper().replace("-", "_").replace(" ", "_")
    if "CROSS_SITE_SCRIPT" in normalized or normalized in {"XSS", "CWE_79"}:
        return "CWE-79"
    return risk_type[:120] or "UNCLASSIFIED"


def _severity(value: str) -> str:
    normalized = value.upper()
    return normalized if normalized in {"INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"} else "INFO"


def normalize_report(raw: dict) -> dict:
    findings = raw.get("findings") if isinstance(raw, dict) else None
    if not isinstance(findings, list) or not findings or not isinstance(findings[0], dict):
        return raw
    if "findingId" not in findings[0]:
        return raw
    aws = AWSReport.model_validate(raw)
    converted = []
    for finding in aws.findings:
        location = finding.codeLocations[0] if finding.codeLocations else AWSCodeLocation()
        description = finding.description
        if finding.reasoning:
            description = (description + "\n판단 근거: " + finding.reasoning).strip()
        converted.append({
            "id": finding.findingId,
            "title": finding.name,
            "cwe": _cwe(finding.riskType),
            "severity": _severity(finding.riskLevel),
            "description": description[:4000] or "설명 없음",
            "source_file": location.filePath,
            "line": location.lineStart,
            "trigger": None,
            "payload": None,
        })
    return {
        "source": "aws-security-agent",
        "source_label": aws.source_label,
        "findings": converted,
    }
