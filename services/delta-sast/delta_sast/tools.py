"""관리 에이전트용 도구 정의. tools는 LLM 도구 형식 그대로 넘기고, routes로 HTTP 호출에 연결한다."""

from __future__ import annotations

from typing import Any

from .contract import RUN_ID, DastFeedback, ScanRequest


def _schema(model) -> dict[str, Any]:
    schema = model.model_json_schema()
    schema.pop("title", None)
    return schema


def _run_id_schema() -> dict[str, Any]:
    return {"type": "object", "properties": {"run_id": {"type": "string", "pattern": RUN_ID,
                                                         "description": "파이프라인 실행 ID"}},
            "required": ["run_id"], "additionalProperties": False}


def tool_catalog() -> dict[str, Any]:
    feedback = _schema(DastFeedback)
    feedback["properties"] = {"run_id": _run_id_schema()["properties"]["run_id"], **feedback["properties"]}
    feedback["required"] = ["run_id", *feedback.get("required", [])]
    return {
        "tools": [
            {
                "name": "sast_scan",
                "description": (
                    "저장소 커밋의 소스를 정적 점검한다. 빌드와 동시에 부른다. 바로 state=running을 돌려주고 "
                    "보통 1분 안에 done이 된다. 같은 run_id로 다시 부르면 새로 점검하지 않는다."
                ),
                "input_schema": _schema(ScanRequest),
            },
            {
                "name": "sast_status",
                "description": (
                    "점검 상태와 요약을 조회한다. summary.dast_targets가 DAST가 공격할 결과 수다. "
                    "0이면 DAST를 건너뛸 수 있다(이전 실행의 회귀 대상이 없을 때만)."
                ),
                "input_schema": _run_id_schema(),
            },
            {
                "name": "sast_sarif",
                "description": "점검 결과 SARIF 2.1.0 원문. DAST에 그대로 넘긴다. done일 때만 받을 수 있다.",
                "input_schema": _run_id_schema(),
            },
            {
                "name": "sast_dast_feedback",
                "description": (
                    "DAST 재현 결과를 SAST에 알린다. LLM 판정과 DAST 결과가 맞으면 그 필터 판정을 규칙으로 굳혀 "
                    "다음 점검부터 LLM 없이 판정한다. fingerprint는 SARIF partialFingerprints[\"deltaFlow/v1\"]."
                ),
                "input_schema": feedback,
            },
        ],
        "routes": {
            "sast_scan": {"method": "POST", "path": "/v1/sast/scans"},
            "sast_status": {"method": "GET", "path": "/v1/sast/scans/{run_id}"},
            "sast_sarif": {"method": "GET", "path": "/v1/sast/scans/{run_id}/sarif"},
            "sast_dast_feedback": {"method": "POST", "path": "/v1/sast/scans/{run_id}/dast-results"},
        },
    }
