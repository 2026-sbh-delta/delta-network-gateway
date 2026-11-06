"""Operator-only case management. The publishing credential has no review authority."""

import hmac
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from delta_ai.case_evaluation import EvaluationDataset, export_candidate
from delta_ai.cases import CaseConflict, CaseReview, Hash, IncidentCase
from delta_ai.diagnostic_contract import Identifier, SafeModel


class RevisionCommand(SafeModel):
    revision: Hash


class ReviewCommand(RevisionCommand):
    review: CaseReview


def case_view(row, *, detail=False):
    case = IncidentCase.model_validate_json(row["payload"])
    review = CaseReview.model_validate_json(row["review"]) if row["review"] else None
    result = {
        k: row[k]
        for k in (
            "case_id",
            "revision",
            "sequence",
            "created_at",
            "run_id",
            "service_id",
            "node_id",
            "environment",
        )
    }
    result.update(
        outcome=case.resolution.outcome,
        summary=case.report.summary,
        review=review.model_dump(mode="json") if review else None,
        approvable=case.resolution.outcome in ("success", "failure")
        and case.request.context is not None,
        exportable=bool(
            review and review.decision == "approved" and review.required_terms
        ),
    )
    if detail:
        result.update(
            case=case.model_dump(mode="json"),
            **{
                k: row[k]
                for k in (
                    "versions",
                    "review_events",
                    "versions_truncated",
                    "review_events_truncated",
                )
            },
        )
    return result


def review_router(store, token):
    bearer = HTTPBearer(auto_error=False)

    def require_review(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ):
        supplied = credentials.credentials if credentials else ""
        if not token or not hmac.compare_digest(supplied.encode(), token.encode()):
            raise HTTPException(401, "검토 권한 인증 실패")

    router = APIRouter(
        prefix="/v1/case-reviews", dependencies=[Depends(require_review)]
    )

    @router.get("")
    def listing(
        limit: Annotated[int, Query(ge=1, le=50)] = 30,
        offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    ):
        try:
            rows = store.list(limit=limit, offset=offset)
            return {
                "items": [case_view(r) for r in rows],
                "offset": offset,
                "next_offset": offset + limit if len(rows) == limit else None,
            }
        except (OSError, ValueError, sqlite3.Error):
            raise HTTPException(503, "사례 조회 불가") from None

    @router.get("/{case_id}")
    def detail(case_id: Identifier):
        try:
            row = store.detail(case_id)
            if row is None:
                raise HTTPException(404, "사례 없음")
            return case_view(row, detail=True)
        except (OSError, ValueError, sqlite3.Error):
            raise HTTPException(503, "사례 조회 불가") from None

    @router.post("/{case_id}/review")
    def review(case_id: Identifier, command: ReviewCommand):
        try:
            store.review(case_id, command.revision, command.review)
            return {"case_id": case_id, "revision": command.revision, "reviewed": True}
        except CaseConflict:
            raise HTTPException(409, "최신 버전과 재검사 결과를 확인하세요") from None
        except (OSError, ValueError, sqlite3.Error):
            raise HTTPException(503, "사례 검토 저장 불가") from None

    @router.post("/{case_id}/export")
    def export(case_id: Identifier, command: RevisionCommand):
        try:
            dataset = EvaluationDataset(
                candidates=(
                    export_candidate(store, case_id, revision=command.revision),
                )
            )
            return Response(
                dataset.model_dump_json(),
                media_type="application/json",
                headers={
                    "Content-Disposition": f'attachment; filename="evaluation-{case_id}.json"',
                    "Cache-Control": "no-store",
                },
            )
        except (CaseConflict, ValueError):
            raise HTTPException(
                409, "최신 버전의 승인과 독립 기대 확인 항목이 필요합니다"
            ) from None
        except (OSError, sqlite3.Error):
            raise HTTPException(503, "평가셋 추출 불가") from None

    return router
