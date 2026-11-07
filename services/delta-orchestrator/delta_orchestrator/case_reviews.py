"""Console operator adapter; the graph never receives the review credential."""

import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from delta_ai.case_review_api import ReviewCommand, RevisionCommand
from delta_ai.diagnostic_contract import Identifier
from delta_pipeline.diagnostic_client import DiagnosticClient
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool


class CaseReviewClient(DiagnosticClient):
    def request(self, method, path, body=None):
        payload = (
            json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        )
        request = urllib.request.Request(
            self._url + "/v1/case-reviews" + path,
            payload,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with self._opener.open(request, timeout=10) as response:
                raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise ValueError()
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise TypeError()
            return result
        except urllib.error.HTTPError as exc:
            status = exc.code if exc.code in (404, 409, 422) else 503
            messages = {
                404: "사례를 찾지 못했습니다",
                409: "최신 버전의 검증 결과·승인·기대 항목을 확인하세요",
                422: "검토 입력 형식을 확인하세요",
                503: "사례 검토 서비스에 연결할 수 없습니다",
            }
            raise HTTPException(status, messages[status]) from None
        except (OSError, ValueError, TypeError):
            raise HTTPException(503, "사례 검토 서비스에 연결할 수 없습니다") from None

    @classmethod
    def from_env(cls):
        token = os.environ.get("DELTA_CASE_REVIEW_TOKEN", "")
        producer = os.environ.get("DELTA_DIAGNOSTIC_TOKEN", "")
        if not token or (
            producer and hmac.compare_digest(token.encode(), producer.encode())
        ):
            raise HTTPException(503, "사례 검토 전용 연결이 설정되지 않았습니다")
        try:
            return cls(
                os.environ.get("DELTA_DIAGNOSTIC_URL", ""),
                token,
                os.environ.get("DELTA_DIAGNOSTIC_CA_BASE64"),
            )
        except ValueError:
            raise HTTPException(
                503, "사례 검토 전용 연결이 설정되지 않았습니다"
            ) from None


def console_case_router(client_factory=CaseReviewClient.from_env):
    router = APIRouter(prefix="/api/case-reviews")

    async def command(request, schema):
        if (
            request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            != "application/json"
        ):
            raise HTTPException(415, "JSON 형식으로 전송하세요")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 16000:
                raise HTTPException(413, "검토 입력이 너무 큽니다")
        try:
            return schema.model_validate_json(bytes(raw)).model_dump(mode="json")
        except ValidationError:
            raise HTTPException(422, "검토 입력 형식을 확인하세요") from None

    async def call(method, path, body=None):
        return await run_in_threadpool(
            lambda: client_factory().request(method, path, body)
        )

    @router.get("")
    async def listing(limit: int = 30, offset: int = 0):
        if not 1 <= limit <= 50 or not 0 <= offset <= 100000:
            raise HTTPException(422, "목록 범위를 확인하세요")
        return await call("GET", f"?limit={limit}&offset={offset}")

    @router.get("/{case_id}")
    async def detail(case_id: Identifier):
        return await call("GET", "/" + urllib.parse.quote(case_id, safe=""))

    @router.post("/{case_id}/review")
    async def review(case_id: Identifier, request: Request):
        body = await command(request, ReviewCommand)
        return await call(
            "POST", f"/{urllib.parse.quote(case_id, safe='')}/review", body
        )

    @router.post("/{case_id}/export")
    async def export(case_id: Identifier, request: Request):
        body = await command(request, RevisionCommand)
        result = await call(
            "POST", f"/{urllib.parse.quote(case_id, safe='')}/export", body
        )
        # Validate the pinned dataset before it crosses the browser download boundary.
        from delta_ai.case_evaluation import EvaluationDataset

        try:
            dataset = EvaluationDataset.model_validate(result)
            (candidate,) = dataset.candidates
            from delta_ai.cases import digest

            if (
                candidate.case.request.case_id != case_id
                or candidate.revision != body["revision"]
                or digest(candidate.case) != candidate.revision
                or candidate.review.decision != "approved"
                or not candidate.review.required_terms
            ):
                raise ValueError()
        except (ValueError, ValidationError):
            raise HTTPException(503, "평가셋 응답을 검증하지 못했습니다") from None
        return Response(
            dataset.model_dump_json(),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="evaluation-{case_id}.json"',
                "Cache-Control": "no-store",
            },
        )

    return router
