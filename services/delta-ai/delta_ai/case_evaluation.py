"""Human-labelled, frozen diagnosis evaluation with held-out incident exclusion."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from delta_ai.cases import CaseReview, IncidentCase, digest, observation_signature, select_cases
from delta_ai.diagnostic_contract import SafeModel


class EvaluationCandidate(SafeModel):
    case: IncidentCase
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    review: CaseReview


class EvaluationDataset(SafeModel):
    schema_version: Literal[1] = 1
    candidates: tuple[EvaluationCandidate, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def distinct_cases(self):
        if len({c.case.request.case_id for c in self.candidates}) != len(self.candidates):
            raise ValueError("평가 사례가 중복됐다")
        return self


def export_candidate(store, case_id: str, *, revision: str | None = None):
    # CLI and authenticated console share the same independently authored labels.
    with store.connection() as db:
        row = db.execute("""SELECT c.*,r.payload AS review FROM case_versions c
            JOIN case_reviews r ON r.revision=c.revision WHERE c.case_id=?
            ORDER BY c.sequence DESC LIMIT 1""", (case_id,)).fetchone()
        latest = db.execute("SELECT MAX(sequence) FROM case_versions WHERE case_id=?", (case_id,)).fetchone()[0]
    if row is None or row["sequence"] != latest:
        raise ValueError("최신 사례 검토가 필요하다")
    if revision is not None and row["revision"] != revision:
        from delta_ai.cases import CaseConflict
        raise CaseConflict("최신 사례 버전을 확인해야 한다")
    review = CaseReview.model_validate_json(row["review"])
    if review.decision != "approved" or not review.required_terms:
        raise ValueError("승인 및 사람이 작성한 기대 확인 항목이 필요하다")
    return EvaluationCandidate(case=IncidentCase.model_validate_json(row["payload"]),
                               revision=row["revision"], review=review)


async def evaluate(dataset, *, llm, store):
    # Console export only needs the dataset contract, not model/knowledge resources.
    from delta_ai.diagnostics import diagnose

    held_out_ids = {c.case.request.case_id for c in dataset.candidates}
    held_out_signatures = {observation_signature(c.case.request) for c in dataset.candidates}
    # Materialize before the first LLM call; concurrent reviews cannot change this evaluation.
    frozen = {}
    for candidate in dataset.candidates:
        if (digest(candidate.case) != candidate.revision or candidate.review.decision != "approved"
                or not candidate.review.required_terms):
            raise ValueError("평가셋 버전 또는 사람이 작성한 기대값이 유효하지 않다")
        request = candidate.case.request
        frozen[request.case_id] = store.approved(request)
    results = []
    for candidate in dataset.candidates:
        request = candidate.case.request
        history = select_cases(frozen[request.case_id], request, excluded_cases=held_out_ids,
                               excluded_signatures=held_out_signatures)
        outputs = {}
        for mode, cases in (("baseline", []), ("with_cases", history)):
            report = await diagnose(request, llm, historical_cases=cases)
            text = report.summary + "\n" + "\n".join(report.next_checks) + "\n" + "\n".join(h.explanation for h in report.hypotheses)
            missing = [t for t in candidate.review.required_terms if t not in text]
            forbidden = [t for t in candidate.review.forbidden_terms if t in text]
            outputs[mode] = {"passed": report.status == "analyzed" and not missing and not forbidden,
                "missing_terms": missing, "forbidden_terms": forbidden, "analysis_status": report.status,
                "retrieved_case_ids": [c.case_id for c in report.related_cases],
                "knowledge_snapshot_hash": report.knowledge_snapshot_hash}
        results.append({"case_id": request.case_id, **outputs})
    return {"schema_version": 1, "dataset_sha256": digest(dataset), "cases": results,
            "baseline_passed": sum(r["baseline"]["passed"] for r in results),
            "with_cases_passed": sum(r["with_cases"]["passed"] for r in results),
            "total": len(results), "limitation": "검토자가 지정한 문자열/근거 계약 평가다. 의미적 원인 정확도 전체를 측정하지 않는다."}
