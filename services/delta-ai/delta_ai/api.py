"""HTTP 진입점. uvicorn delta_ai.api:app 으로 띄운다.

환경 변수: DELTA_API_TOKEN(필수), DELTA_LLM=none|fake|claude(기본 none), DELTA_RULE_STORE(기본 var/rule_candidates.json),
DELTA_LLM_TIMEOUT_SECONDS(기본 60).
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
from collections.abc import Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from delta_ai.contract import (
    AssessmentRequest,
    AssessmentResult,
    CandidateDecision,
    RuleCandidate,
)
from delta_ai.diagnostic_contract import (
    DiagnosisDraft,
    DiagnosisReport,
    DiagnosisRequest,
)
from delta_ai.diagnostics import FakeDiagnosticLLM, diagnose
from delta_ai.recovery_contract import RecoveryDraft, RecoveryProposal, RecoveryRequest
from delta_ai.recovery_proposals import FakeRecoveryLLM, propose
from delta_ai.errors import TransientError
from delta_ai.execution import AssessmentLedger, ExecutionConflict, payload_hash
from delta_ai.fake_llm import FakeLLM
from delta_ai.judge import LlmOutput
from delta_ai.llm import ClaudeLLM, LlmClient
from delta_ai.service import assess
from delta_ai.store import CandidateNotFound, CandidateNotReady, RuleStore
from delta_ai.cases import CaseConflict, CaseStore, IncidentCase

MAX_BODY_BYTES = 10_000_000
DEFAULT_LLM_TIMEOUT_SECONDS = "60"
LLM_MAX_RETRIES = 1  # SDK 기본 2회면 시간 초과까지 재시도해 호출자가 오래 매달린다
logger = logging.getLogger(__name__)


def create_app(
    *,
    llm: LlmClient | None,
    store: RuleStore,
    token: str,
    diagnostic_llm: LlmClient | None = None,
    recovery_llm: LlmClient | None = None,
    case_store: CaseStore | None = None,
    review_token: str | None = None,
) -> FastAPI:
    if not token:
        raise ValueError("DELTA_API_TOKEN 이 필요하다")
    if review_token and hmac.compare_digest(review_token.encode(), token.encode()):
        raise ValueError("사례 검토 토큰은 생산자 토큰과 달라야 한다")
    app = FastAPI(title="Delta AI 판단 서비스", version="0.1.0")
    bearer = HTTPBearer(auto_error=False)
    ledger = AssessmentLedger(store.assessment_directory)
    assessment_lock = asyncio.Lock()
    incidents = case_store or CaseStore(store.assessment_directory.parent / "verified-cases.sqlite")

    from delta_ai.case_review_api import review_router
    app.include_router(review_router(incidents, review_token))

    def require_token(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),  # noqa: B008
    ) -> None:
        supplied = credentials.credentials if credentials else ""
        if not hmac.compare_digest(supplied.encode(), token.encode()):
            raise HTTPException(status_code=401, detail="인증 실패")

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        if request.url.path.startswith("/v1/case-reviews") or request.url.path in ("/v1/diagnoses", "/v1/recovery-proposals", "/v1/incident-cases", "/v1/migration-mapping"):
            return JSONResponse(
                status_code=422, content={"detail": "진단 요청 형식이 잘못됐다"}
            )
        return await request_validation_exception_handler(request, exc)

    @app.post("/v1/diagnoses", dependencies=[Depends(require_token)])
    async def create_diagnosis(req: DiagnosisRequest) -> DiagnosisReport:
        async with assessment_lock:
            return await diagnose(req, diagnostic_llm, case_store=incidents)

    @app.post("/v1/incident-cases", dependencies=[Depends(require_token)])
    async def publish_incident(case: IncidentCase):
        # The producer credential cannot use the separate operator review endpoints.
        try:
            revision = incidents.publish(case)
            return {"case_id": case.request.case_id, "revision": revision, "review": "required"}
        except CaseConflict as exc:
            raise HTTPException(status_code=409, detail="사례 버전 충돌") from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=503, detail="사례 저장 불가") from exc

    @app.post("/v1/recovery-proposals", dependencies=[Depends(require_token)])
    async def recovery_proposal(req: RecoveryRequest) -> RecoveryProposal:
        async with assessment_lock:
            return await propose(req, recovery_llm)

    @app.middleware("http")
    async def limit_body(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        declared = request.headers.get("content-length", "0")
        if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            return JSONResponse(status_code=413, content={"detail": "요청이 너무 크다"})
        if request.url.path.startswith("/v1/case-reviews") or request.url.path in ("/v1/diagnoses", "/v1/recovery-proposals", "/v1/incident-cases"):
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 64_000:
                    return JSONResponse(
                        status_code=413, content={"detail": "진단 요청이 너무 크다"}
                    )
            request._body = bytes(body)
        return await call_next(request)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/migration-mapping", dependencies=[Depends(require_token)])
    async def migration_mapping(req: AssessmentRequest) -> AssessmentResult:
        if req.kind != "migration_mapping" or req.execution_context is not None:
            raise HTTPException(status_code=422, detail="이 경로는 구성 매핑 요청만 받습니다")
        return await assess(req, llm=None, store=store)

    @app.post("/v1/assessments", dependencies=[Depends(require_token)])
    async def create_assessment(
        req: AssessmentRequest, request: Request
    ) -> AssessmentResult:
        try:
            async with assessment_lock:
                if not req.execution_context:
                    return await assess(req, llm=llm, store=store)
                raw = await request.json()
                body = {
                    k: v
                    for k, v in raw.items()
                    if k not in ("assessment_id", "execution_context")
                }
                if payload_hash(body) != req.execution_context.payload_hash:
                    raise HTTPException(
                        status_code=409, detail="실행 입력 해시가 다르다"
                    )
                cached = ledger.bind(req.execution_context, raw)
                if cached is not None:
                    return AssessmentResult.model_validate(cached)
                result = await assess(req, llm=llm, store=store)
                result = result.model_copy(
                    update={"execution_context": req.execution_context}
                )
                ledger.complete(req.execution_context, result.model_dump(mode="json"))
                return result
        except ExecutionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except TransientError as exc:
            logger.warning("LLM 일시 오류: %s", exc)
            raise HTTPException(
                status_code=503, detail="LLM 일시 오류 — 다시 시도한다"
            ) from exc

    @app.get("/v1/rule-candidates", dependencies=[Depends(require_token)])
    async def list_candidates() -> tuple[RuleCandidate, ...]:
        return store.list_candidates()

    @app.post(
        "/v1/rule-candidates/{candidate_id}/decision",
        dependencies=[Depends(require_token)],
    )
    async def decide_candidate(
        candidate_id: str, body: CandidateDecision
    ) -> RuleCandidate:
        try:
            return store.decide(
                candidate_id, decision=body.decision, approver=body.approver
            )
        except CandidateNotFound as exc:
            raise HTTPException(status_code=404, detail="후보 없음") from exc
        except CandidateNotReady as exc:
            raise HTTPException(
                status_code=409, detail="탐지기가 없거나 회귀 시험을 통과하지 못했다"
            ) from exc

    return app


def _llm_from_env() -> LlmClient | None:
    mode = os.environ.get("DELTA_LLM", "none")
    if mode == "none":
        return None
    if mode == "fake":
        return FakeLLM()
    if mode == "claude":
        timeout = float(
            os.environ.get("DELTA_LLM_TIMEOUT_SECONDS", DEFAULT_LLM_TIMEOUT_SECONDS)
        )
        return ClaudeLLM(
            output=LlmOutput,
            client_options={"timeout": timeout, "max_retries": LLM_MAX_RETRIES},
        )
    raise ValueError(f"DELTA_LLM 값이 잘못됐다: {mode}")


def _diagnostic_llm_from_env() -> LlmClient | None:
    mode = os.environ.get("DELTA_LLM", "none")
    if mode == "none":
        return None
    if mode == "fake":
        return FakeDiagnosticLLM()
    if mode == "claude":
        timeout = float(
            os.environ.get("DELTA_LLM_TIMEOUT_SECONDS", DEFAULT_LLM_TIMEOUT_SECONDS)
        )
        return ClaudeLLM(
            output=DiagnosisDraft,
            client_options={"timeout": timeout, "max_retries": LLM_MAX_RETRIES},
        )
    raise ValueError("DELTA_LLM 값이 잘못됐다")


def _recovery_llm_from_env():
    mode = os.environ.get('DELTA_LLM', 'none')
    if mode == 'fake':
        return FakeRecoveryLLM()
    if mode == 'claude':
        return ClaudeLLM(output=RecoveryDraft, client_options={
            'timeout': float(os.environ.get('DELTA_LLM_TIMEOUT_SECONDS', DEFAULT_LLM_TIMEOUT_SECONDS)),
            'max_retries': LLM_MAX_RETRIES})
    return None


def _app_from_env() -> FastAPI:
    return create_app(
        review_token=os.environ.get("DELTA_CASE_REVIEW_TOKEN") or None,
        llm=_llm_from_env(),
        store=RuleStore(os.environ.get("DELTA_RULE_STORE", "var/rule_candidates.json")),
        token=os.environ.get("DELTA_API_TOKEN", ""),
        diagnostic_llm=_diagnostic_llm_from_env(),
        recovery_llm=_recovery_llm_from_env(),
        case_store=CaseStore(os.environ["DELTA_CASE_STORE"]) if os.environ.get("DELTA_CASE_STORE") else None,
    )


def __getattr__(name: str) -> FastAPI:
    """uvicorn delta_ai.api:app 에서만 환경 변수를 읽는다. import 만 해서는 토큰이 필요 없다."""
    if name == "app":
        return _app_from_env()
    raise AttributeError(name)
