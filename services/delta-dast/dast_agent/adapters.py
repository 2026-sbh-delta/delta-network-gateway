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


# ---------- Delta SAST SARIF ----------

SARIF_CWE = {"delta.xss.stored": "CWE-79", "delta.xss.reflected": "CWE-79"}
SARIF_TITLE = {"delta.xss.stored": "저장형 XSS", "delta.xss.reflected": "반사형 XSS"}
SARIF_ID_PREFIX = "sast-"
MAX_FINDINGS = 10


def is_sarif(raw: dict) -> bool:
    return isinstance(raw, dict) and raw.get("version") == "2.1.0" and isinstance(raw.get("runs"), list)


def _route(value: dict | None) -> str:
    return f"{value['method']} {value['path']}" if value else "알 수 없음"


def _steps(result: dict, limit: int = 10) -> list[str]:
    flows = result.get("codeFlows") or []
    locations = flows[0]["threadFlows"][0]["locations"] if flows else []
    lines = []
    for item in locations[:limit]:
        phys = item["location"]["physicalLocation"]
        note = item["location"].get("message", {}).get("text", "")
        lines.append(f"- {phys['artifactLocation']['uri']}:{phys['region']['startLine']} {note}".rstrip())
    return lines


def sarif_targets(raw: dict) -> list[dict]:
    """DAST가 공격할 결과만. kind가 fail·review이고, DAST로 재현 가능하고, 지금 DAST가 다루는 XSS인 것."""
    out = []
    for result in raw["runs"][0].get("results", []):
        dast = (result.get("properties") or {}).get("dast") or {}
        if result.get("kind") in ("fail", "review") and dast.get("verifiable") and result.get("ruleId") in SARIF_CWE:
            out.append(result)
    # 저장형·확정 후보를 먼저 본다
    out.sort(key=lambda r: (r["ruleId"] != "delta.xss.stored", r["kind"] != "fail"))
    return out


def from_sarif(raw: dict) -> dict:
    """Delta SAST SARIF → 내부 형식. finding id는 sast-<경로 ID>라서 결과를 SAST로 되돌릴 수 있다."""
    run = raw["runs"][0]
    targets = sarif_targets(raw)
    if not targets:
        raise ValueError("SARIF에 DAST로 확인할 XSS 결과가 없다")
    converted = []
    for result in targets[:MAX_FINDINGS]:
        props = result["properties"]
        dast, judgment = props["dast"], props.get("judgment") or {}
        sink = result["locations"][0]["physicalLocation"]
        field = f"{dast.get('input')}.{dast.get('field') or '*'}"
        title = f"{SARIF_TITLE[result['ruleId']]}: {field} ({_route(dast.get('entry'))} → {_route(dast.get('render'))})"
        hints = [
            result["message"]["text"],
            "",
            f"입력 라우트: {_route(dast.get('entry'))}",
            f"입력 칸: {field}",
            f"출력 라우트: {_route(dast.get('render'))}",
            f"출력 맥락: {dast.get('context')}",
            f"저장형: {'예' if dast.get('stored') else '아니오'}",
            f"SAST 판정: {judgment.get('by')}/{judgment.get('verdict')} — {judgment.get('reason', '')}",
        ]
        if dast.get("payload"):
            hints.append(f"SAST 제안 페이로드 틀: {dast['payload']}")
        hints += ["", "SAST 경로(입력 → 출력):", *_steps(result)]
        converted.append({
            "id": SARIF_ID_PREFIX + result["partialFingerprints"]["deltaFlow/v1"],
            "title": title[:200],
            "cwe": SARIF_CWE[result["ruleId"]],
            "severity": "HIGH" if result["kind"] == "fail" and dast.get("stored") else "MEDIUM",
            "description": "\n".join(hints)[:4000],
            "source_file": sink["artifactLocation"]["uri"][:300],
            "line": sink["region"]["startLine"],
            "trigger": None,
            "payload": None,
        })
    run_id = (run.get("properties") or {}).get("run_id", "")
    return {"source": "delta-sast", "source_label": f"Delta SAST SARIF {run_id}".strip()[:200],
            "findings": converted}


def normalize_report(raw: dict) -> dict:
    if is_sarif(raw):
        return from_sarif(raw)
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
