"""관리 에이전트용 도구 정의. tools 는 LLM 도구 형식 그대로 넘기고, routes 로 HTTP 호출에 연결한다."""

from __future__ import annotations

from typing import Any

from delta_staging.contract import DeployRequest

_RUN_ID = {"type": "string", "pattern": r"^[A-Za-z0-9._-]{1,64}$", "description": "파이프라인 실행 ID"}


def _run_id_schema() -> dict[str, Any]:
    return {"type": "object", "properties": {"run_id": _RUN_ID}, "required": ["run_id"], "additionalProperties": False}


def tool_catalog() -> dict[str, Any]:
    deploy_schema = DeployRequest.model_json_schema()
    deploy_schema.pop("title", None)
    return {
        "tools": [
            {
                "name": "staging_deploy",
                "description": (
                    "빌드된 컨테이너 이미지를 지정한 클라우드의 스테이징에 띄운다. 바로 state=deploying 을 돌려주고 "
                    "몇 분 안에 ready 가 된다. ready 의 url 은 DAST 스캐너만 닿는 내부 주소다. "
                    "같은 run_id 로 다시 부르면 새로 띄우지 않는다. 다른 실행이 쓰는 중이면 replace=true 로 교체한다."
                ),
                "input_schema": deploy_schema,
            },
            {
                "name": "staging_status",
                "description": "스테이징 배포 상태와 단계별 기록을 조회한다. state 가 ready 면 url 을 DAST 에 넘긴다.",
                "input_schema": _run_id_schema(),
            },
            {
                "name": "staging_stop",
                "description": (
                    "스테이징을 내린다(AWS 는 태스크 0개와 스캐너 IP 규칙 삭제, GCP 는 Cloud Armor 스캐너 규칙 삭제). "
                    "스테이징은 상시로 띄워 두고 실행마다 "
                    "staging_deploy 로 이미지만 바꾸므로 점검 회차마다 부르지 않는다. 시연·작업 세션을 마칠 때만 부른다."
                ),
                "input_schema": _run_id_schema(),
            },
        ],
        "routes": {
            "staging_deploy": {"method": "POST", "path": "/v1/staging/deployments"},
            "staging_status": {"method": "GET", "path": "/v1/staging/deployments/{run_id}"},
            "staging_stop": {"method": "POST", "path": "/v1/staging/deployments/{run_id}/stop"},
        },
    }
