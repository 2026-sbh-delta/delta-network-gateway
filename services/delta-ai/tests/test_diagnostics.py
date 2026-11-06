import json

import pytest
from delta_ai.api import create_app
from delta_ai.diagnostic_contract import (
    DiagnosisReport,
    DiagnosisRequest,
    diagnosis_hash,
)
from delta_ai.diagnostics import diagnose
from delta_ai.errors import TransientError
from delta_ai.fake_llm import FakeLLM
from delta_ai.llm import LlmResponse
from delta_ai.store import RuleStore
from fastapi.testclient import TestClient

AUTH = {"Authorization": "Bearer test-diagnostic-token"}
BODY = {
    "case_id": "case-1",
    "run_id": "run-1",
    "service_id": "notes",
    "node_id": "infrastructure_diff",
    "trigger": "ambiguous_result",
    "observations": [
        {
            "evidence_id": "e1",
            "source": "delta-diff",
            "summary": "PostgreSQL 원본 16, 대상 15로 주 버전이 다르다",
            "rule_ids": ["MAP-DB-001"],
        }
    ],
}
DRAFT = {
    "summary": "주 버전 차이가 있어 복원 호환성을 확인해야 한다.",
    "hypotheses": [
        {
            "explanation": "주 버전 차이가 원인 후보이다.",
            "confidence": "medium",
            "evidence_ids": ["e1"],
            "rule_ids": ["MAP-DB-001"],
        }
    ],
    "next_checks": ["덤프와 대상 PostgreSQL 주 버전 및 확장을 확인한다."],
    "limitations": ["실제 DB 복원 시험은 수행하지 않았다."],
}


class FakeAnalysis:
    model = "diagnosis-test"

    def __init__(self, draft=None, error=None):
        self.requests, self.draft, self.error = (
            [],
            DRAFT if draft is None else draft,
            error,
        )

    async def complete(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return LlmResponse(text=json.dumps(self.draft), model=self.model)


def api(tmp_path, diagnostic=None):
    return TestClient(
        create_app(
            llm=FakeLLM(),
            diagnostic_llm=diagnostic,
            store=RuleStore(tmp_path / "rules.json"),
            token="test-diagnostic-token",
        )
    )


async def test_diagnosis_returns_grounded_analysis_and_has_no_control_plane_authority():
    llm = FakeAnalysis()
    request = DiagnosisRequest.model_validate(BODY)
    report = await diagnose(request, llm)
    assert report.status == "analyzed"
    assert report.requires_human is True
    assert report.input_hash == diagnosis_hash(request)
    assert report.hypotheses[0].evidence_ids == ("e1",)
    assert "MAP-DB-001" in llm.requests[0].user
    assert (
        not {"next_node", "retry", "approve", "commands", "patch"}
        & report.model_dump().keys()
    )


@pytest.mark.parametrize(
    "patch",
    [
        {
            "hypotheses": [
                {
                    "explanation": "무근거",
                    "confidence": "high",
                    "evidence_ids": ["never-seen"],
                }
            ]
        },
        {
            "hypotheses": [
                {
                    "explanation": "무근거",
                    "confidence": "high",
                    "evidence_ids": ["e1"],
                    "rule_ids": ["MAP-NOT-REAL"],
                }
            ]
        },
        {"next_node": "release_start"},
        {"approve": True},
        {"requires_human": False},
    ],
)
async def test_unverified_or_authoritative_llm_output_becomes_invalid_analysis(patch):
    report = await diagnose(
        DiagnosisRequest.model_validate(BODY), FakeAnalysis({**DRAFT, **patch})
    )
    assert report.status == "invalid_analysis"
    assert report.requires_human is True
    assert report.hypotheses == ()


@pytest.mark.parametrize(
    "llm",
    [
        None,
        FakeAnalysis(error=TransientError("token=PRIVATE")),
        FakeAnalysis(error=RuntimeError("password=PRIVATE")),
    ],
)
async def test_analysis_unavailable_is_explicit_and_does_not_expose_exception(llm):
    report = await diagnose(DiagnosisRequest.model_validate(BODY), llm)
    assert report.status == "analysis_unavailable"
    assert "PRIVATE" not in report.model_dump_json()


def test_api_requires_token_and_does_not_run_llm_when_unauthorized(tmp_path):
    llm = FakeAnalysis()
    client = api(tmp_path, llm)
    assert client.post("/v1/diagnoses", json=BODY).status_code == 401
    assert (
        client.post(
            "/v1/diagnoses", json=BODY, headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )
    assert llm.requests == []


def test_api_has_no_persistence_side_effect_and_returns_new_contract(tmp_path):
    response = api(tmp_path, FakeAnalysis()).post(
        "/v1/diagnoses", json=BODY, headers=AUTH
    )
    assert response.status_code == 200
    report = DiagnosisReport.model_validate(response.json())
    assert report.status == "analyzed"
    assert list(tmp_path.iterdir()) == []


def test_secrets_are_redacted_before_llm_and_in_response(tmp_path):
    llm = FakeAnalysis(
        {
            **DRAFT,
            "summary": "password=RESPONSE-SECRET Authorization: Bearer RESPONSE-TOKEN",
        }
    )
    body = {
        **BODY,
        "observations": [
            {
                "evidence_id": "e1",
                "source": "delta-diff",
                "summary": "postgres://admin:INPUT-SECRET@db password=OTHER-SECRET Bearer INPUT-TOKEN",
            }
        ],
    }
    response = api(tmp_path, llm).post("/v1/diagnoses", json=body, headers=AUTH)
    assert response.status_code == 200
    prompt = llm.requests[0].user
    assert not any(x in prompt for x in ("INPUT-SECRET", "OTHER-SECRET", "INPUT-TOKEN"))
    assert not any(x in response.text for x in ("RESPONSE-SECRET", "RESPONSE-TOKEN"))


def test_invalid_request_and_chunked_large_body_do_not_echo_secrets(tmp_path):
    client = api(tmp_path)
    response = client.post(
        "/v1/diagnoses", json={**BODY, "token": "SECRET-INPUT"}, headers=AUTH
    )
    assert response.status_code == 422
    assert "SECRET-INPUT" not in response.text
    response = client.post(
        "/v1/diagnoses", content=iter([b"{", b"x" * 65_000, b"}"]), headers=AUTH
    )
    assert response.status_code == 413


@pytest.mark.parametrize('llm', [None, FakeAnalysis()])
async def test_log_collection_limits_survive_model_omission_or_outage(llm):
    request=DiagnosisRequest.model_validate({**BODY,'observations':[
        *BODY['observations'],
        {'evidence_id':'cw-context','source':'cloudwatch','summary':json.dumps({
            'kind':'delta-log-evidence/v1','scope':'environment','status':'unavailable',
            'sample_events':1,'unparsed':3,'truncated':True})}]})
    report=await diagnose(request,llm)
    limits=' '.join(report.limitations)
    assert '정상·안전으로 해석하지 않는다' in limits
    assert '연관성은 확인되지 않았다' in limits and '샘플' in limits
    assert '해석하지 못한 로그' in limits and '전체 이력' in limits
    assert report.requires_human is True
