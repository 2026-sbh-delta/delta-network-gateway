"""Versioned, reviewed incident knowledge. Publishing never grants review authority."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from delta_ai.diagnostic_contract import (
    DiagnosisReport, DiagnosisRequest, HistoricalCaseRef, Identifier, SafeModel, Text,
    diagnosis_hash, validate_report,
)

Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def digest(value) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def observation_signature(request: DiagnosisRequest) -> str:
    """Ignore incident IDs and code revisions: identical symptoms cannot be eval answers."""
    observations = [o.model_dump(mode="json") for o in request.observations]
    for observation in observations:
        observation["summary"] = re.sub(r"\b(?:[0-9a-f]{32,64}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\b",
                                         "<execution-id>", observation["summary"])
        for identifier in (request.case_id, request.run_id):
            observation["summary"] = observation["summary"].replace(identifier, "<execution-id>")
    return digest({"service": request.service_id, "node": request.node_id,
                   "trigger": request.trigger,
                   "observations": observations})


class VerificationCheck(SafeModel):
    check: Identifier
    status: Literal["pass", "fail", "unknown"]
    reference: Identifier
    sha256: Hash
    details: Text | None = None

    @model_validator(mode="after")
    def bound_details(self):
        if self.details is not None and hashlib.sha256(self.details.encode()).hexdigest() != self.sha256:
            raise ValueError("재검사 상세와 근거 해시가 다르다")
        return self


class Resolution(SafeModel):
    outcome: Literal["success", "failure", "unknown", "stopped"] = "unknown"
    action: Identifier = "none"
    attempt: Annotated[int, Field(ge=0, le=100)] = 0
    scope: Identifier
    checks: tuple[VerificationCheck, ...] = Field(default=(), max_length=20)
    limitations: tuple[Text, ...] = Field(default=(
        "성공/실패는 기록된 단계와 검사 범위에 한정된다. 전체 서비스·보안 통과 또는 원인 가설 확정을 뜻하지 않는다.",), max_length=10)

    @model_validator(mode="after")
    def proven_result(self):
        states = {c.status for c in self.checks}
        if self.outcome == "success" and (states != {"pass"} or self.action == "none"):
            raise ValueError("성공은 조치 후 완료된 재검사 근거가 필요하다")
        if self.outcome == "failure" and ("fail" not in states or self.action == "none"):
            raise ValueError("실패는 조치 후 실패한 재검사 근거가 필요하다")
        return self


class IncidentCase(SafeModel):
    request: DiagnosisRequest
    report: DiagnosisReport
    report_sha256: Hash
    sequence: Annotated[int, Field(ge=0, le=1000)] = 0
    resolution: Resolution

    @model_validator(mode="after")
    def consistent(self):
        validate_report(self.report, self.request)
        if self.resolution.scope != self.request.node_id:
            raise ValueError("재검사 범위와 실패 단계가 다르다")
        return self


class CaseReview(SafeModel):
    decision: Literal["approved", "rejected"]
    reviewer: Identifier
    reason: Text
    # These are independently authored checks, never inferred from model hypotheses.
    required_terms: tuple[Annotated[str, Field(min_length=1, max_length=200)], ...] = Field(default=(), max_length=20)
    forbidden_terms: tuple[Annotated[str, Field(min_length=1, max_length=200)], ...] = Field(default=(), max_length=20)


class CaseConflict(ValueError):
    pass


class CaseStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    @contextmanager
    def connection(self):
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise ValueError("사례 저장소에 심볼릭 링크를 허용하지 않는다")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS case_versions (
                    case_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                    revision TEXT NOT NULL UNIQUE, run_id TEXT NOT NULL,
                    service_id TEXT NOT NULL, node_id TEXT NOT NULL, environment TEXT,
                    signature TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(case_id, sequence));
                CREATE TABLE IF NOT EXISTS case_reviews (
                    revision TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS case_review_events (
                    id INTEGER PRIMARY KEY, revision TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS case_lookup ON case_versions(service_id,node_id,environment);
            """)
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def publish(self, case: IncidentCase) -> str:
        revision = digest(case)
        req = case.request
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM case_versions WHERE revision=?", (revision,)).fetchone():
                return revision  # replaying an older immutable version cannot regress the latest
            latest = db.execute("SELECT * FROM case_versions WHERE case_id=? ORDER BY sequence DESC LIMIT 1",
                                (req.case_id,)).fetchone()
            if latest:
                if latest["revision"] == revision:
                    return revision
                original = IncidentCase.model_validate_json(latest["payload"])
                if (case.sequence <= latest["sequence"] or req.run_id != latest["run_id"]
                        or diagnosis_hash(req) != diagnosis_hash(original.request)
                        or digest(case.report) != digest(original.report)
                        or case.report_sha256 != original.report_sha256):
                    raise CaseConflict("사례 버전 또는 원본 바인딩이 다르다")
            db.execute("INSERT INTO case_versions VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (req.case_id, case.sequence, revision, req.run_id, req.service_id, req.node_id,
                        req.context.environment if req.context else None, observation_signature(req),
                        case.model_dump_json(), datetime.now(UTC).isoformat()))
        return revision

    def review(self, case_id: str, revision: str, review: CaseReview):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM case_versions WHERE case_id=? ORDER BY sequence DESC LIMIT 1",
                             (case_id,)).fetchone()
            if row is None or row["revision"] != revision:
                raise CaseConflict("최신 사례 버전을 검토해야 한다")
            case = IncidentCase.model_validate_json(row["payload"])
            if review.decision == "approved" and (case.resolution.outcome not in ("success", "failure")
                                                  or case.request.context is None):
                raise CaseConflict("검증된 조치 결과와 환경 정보가 필요하다")
            saved = db.execute("SELECT payload FROM case_reviews WHERE revision=?", (revision,)).fetchone()
            if saved and saved["payload"] == review.model_dump_json():
                return
            now = datetime.now(UTC).isoformat()
            db.execute("INSERT INTO case_review_events(revision,payload,created_at) VALUES (?,?,?)",
                       (revision, review.model_dump_json(), now))
            db.execute("""INSERT INTO case_reviews VALUES (?,?,?) ON CONFLICT(revision)
                DO UPDATE SET payload=excluded.payload,created_at=excluded.created_at""",
                       (revision, review.model_dump_json(), now))

    def list(self, *, limit: int = 50, offset: int = 0):
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("목록 범위를 확인한다")
        with self.connection() as db:
            rows = db.execute("""SELECT c.*,r.payload AS review FROM case_versions c
                LEFT JOIN case_reviews r ON r.revision=c.revision
                WHERE c.sequence=(SELECT MAX(sequence) FROM case_versions WHERE case_id=c.case_id)
                ORDER BY c.case_id LIMIT ? OFFSET ?""", (limit, offset)).fetchall()
        return [dict(row) for row in rows]

    def approved(self, request: DiagnosisRequest):
        if request.context is None:
            return []
        with self.connection() as db:
            rows = db.execute("""SELECT c.*,r.payload AS review FROM case_versions c
                JOIN case_reviews r ON r.revision=c.revision
                WHERE c.service_id=? AND c.node_id=? AND c.environment=?
                AND c.sequence=(SELECT MAX(sequence) FROM case_versions WHERE case_id=c.case_id)
                AND json_extract(r.payload,'$.decision')='approved' ORDER BY c.case_id LIMIT 200""",
                (request.service_id, request.node_id, request.context.environment)).fetchall()
        return [dict(row) for row in rows]

    def search(self, request, *, excluded_cases=(), excluded_signatures=()):
        return select_cases(self.approved(request), request, excluded_cases=excluded_cases,
                            excluded_signatures=excluded_signatures)


def select_cases(rows, request, *, excluded_cases=(), excluded_signatures=()):
    if request.context is None:
        return []
    signature = observation_signature(request)
    rules = {r for o in request.observations for r in o.rule_ids}
    ranked = []
    for row in rows:
        case = IncidentCase.model_validate_json(row["payload"])
        req = case.request
        if (req.case_id in excluded_cases or req.run_id == request.run_id
                or row["signature"] in {*excluded_signatures, signature}
                or req.context is None or req.context.environment != request.context.environment
                or req.service_id != request.service_id or req.node_id != request.node_id):
            continue
        # Different known error codes describe different incidents, even in the same stage.
        if request.context.error_code and req.context.error_code != request.context.error_code:
            continue
        overlap = len(rules & {r for o in req.observations for r in o.rule_ids})
        if not request.context.error_code and not overlap:
            continue
        ranked.append((overlap, row["case_id"], row, case))
    selected = []
    for _, _, row, case in sorted(ranked, key=lambda x: (-x[0], x[1]))[:3]:
        resolution = case.resolution.model_dump(mode="json", exclude={"checks", "limitations"})
        resolution["limitations"] = [note[:300] for note in case.resolution.limitations[:2]]
        resolution["checks"] = [{"check": c.check, "status": c.status, "reference": c.reference,
                                  "sha256": c.sha256} for c in case.resolution.checks[:3]]
        resolution["checks_truncated"] = len(case.resolution.checks) > 3
        selected.append({"reference": HistoricalCaseRef(case_id=case.request.case_id, run_id=case.request.run_id,
            revision=row["revision"], outcome=case.resolution.outcome, scope=case.resolution.scope).model_dump(mode="json"),
            "symptoms": [o.summary[:500] for o in case.request.observations[:3]],
            "resolution": resolution,
            "review_note": CaseReview.model_validate_json(row["review"]).reason[:1000],
            "limitation": "과거 단계 재검사 결과다. 현재 원인 확정·전체 이전/보안 통과의 증거가 아니다."})
    return selected
