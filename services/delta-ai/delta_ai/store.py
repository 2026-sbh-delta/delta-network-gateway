"""규칙 후보 저장소 — MVP 는 JSON 파일 한 개(프로세스 1개 전제). 공유 레포가 정해지면 DB 로 바꾼다."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from delta_ai.contract import RuleCandidate


class CandidateNotFound(KeyError):
    pass


class CandidateNotReady(ValueError):
    """탐지기가 없거나 회귀 시험을 통과하지 못한 후보는 승인할 수 없다."""


class RuleStore:
    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    @property
    def assessment_directory(self) -> Path:
        return self._path.parent / (self._path.stem + "-assessments")

    def _load(self) -> dict[str, RuleCandidate]:
        if not self._path.exists():
            return {}
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        return {item["candidate_id"]: RuleCandidate.model_validate(item) for item in raw}

    def _save(self, candidates: dict[str, RuleCandidate]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([c.model_dump(mode="json") for c in candidates.values()], ensure_ascii=False, indent=2)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self._path.parent, delete=False) as tmp:
            tmp.write(payload)
        os.replace(tmp.name, self._path)

    def list_candidates(self) -> tuple[RuleCandidate, ...]:
        return tuple(self._load().values())

    def get(self, candidate_id: str) -> RuleCandidate | None:
        return self._load().get(candidate_id)

    def active_rules(self) -> tuple[RuleCandidate, ...]:
        return tuple(c for c in self._load().values() if c.status == "approved")

    def upsert(self, candidate: RuleCandidate) -> None:
        self._save({**self._load(), candidate.candidate_id: candidate})

    def decide(self, candidate_id: str, *, decision: str, approver: str) -> RuleCandidate:
        candidates = self._load()
        candidate = candidates.get(candidate_id)
        if candidate is None:
            raise CandidateNotFound(candidate_id)
        if decision == "approved" and (candidate.status == "needs_detector" or not candidate.regression.ready):
            raise CandidateNotReady(candidate_id)
        updated = candidate.model_copy(update={"status": decision, "approver": approver})
        self._save({**candidates, candidate_id: updated})
        return updated
