"""Read-only migration mapping; persist the assessment before advancing the graph."""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from delta_ai.contract import AssessmentRequest, AssessmentResult
from delta_pipeline.clients import ServiceUnavailable, evidence_hash

MAX_MAPPING_BYTES = 60_000


def normalize_mapping_request(value: Any, *, repo: str, commit: str) -> dict[str, Any]:
    """Accept only extractor output, never a Terraform state or unrelated evidence."""
    if not isinstance(value, dict) or set(value) - {"assessment_id", "kind", "service", "evidence"}:
        raise ValueError("Terraform 추출기의 migration_mapping 요청 JSON을 선택하세요")
    if len(json.dumps(value, ensure_ascii=False).encode()) > MAX_MAPPING_BYTES:
        raise ValueError("매핑 JSON은 60KB 이하여야 합니다")
    try:
        request = AssessmentRequest.model_validate({"assessment_id": "mapping-input", **value})
        if request.kind != "migration_mapping" or request.evidence.mapping is None:
            raise ValueError()
        if set(value.get("evidence", {})) != {"mapping"}:
            raise ValueError()
        if request.service.repo not in (None, repo) or request.service.commit not in (None, commit):
            raise ValueError()
        mapping = request.evidence.mapping
        if len(mapping.servers) > 100 or len(mapping.subnets) > 100:
            raise ValueError()
    except (ValidationError, ValueError, TypeError):
        raise ValueError("매핑 입력 형식 또는 선택한 저장소·커밋을 확인하세요") from None
    # Bind the input to the selected code; uploaded IDs cannot select another run.
    normalized = {"kind": "migration_mapping", "service": {"service_id": request.service.service_id,
                  "repo": repo, "commit": commit},
                  "evidence": {"mapping": mapping.model_dump(mode="json", exclude_none=True)}}
    if len(json.dumps(normalized, ensure_ascii=False).encode()) > MAX_MAPPING_BYTES:
        raise ValueError("정규화한 매핑 JSON은 60KB 이하여야 합니다")
    return normalized


class MappingFlow:
    def __init__(self, deps):
        self.deps = deps

    def assess(self, _state) -> dict[str, Any]:
        deps = self.deps
        body = {**deps.options.mapping_request, "assessment_id": f"map-{deps.run_id}"}
        hashed = evidence_hash(body)
        node = next(n for n in deps.board.snapshot()["nodes"] if n["id"] == "map")
        saved = node["detail"]
        if saved.get("request_sha256") == hashed and saved.get("assessment_id") == body["assessment_id"] and saved.get("mapping"):
            return {"mapping_input_hash": hashed}
        deps.board.update("map", state="running", summary="클라우드 구성·월 예상 비용 계산 중")
        try:
            if deps.mapping_assess is None:
                raise ServiceUnavailable("매핑 서비스가 설정되지 않았습니다")
            response = AssessmentResult.model_validate(deps.mapping_assess(body))
            if response.kind != "migration_mapping" or response.assessment_id != body["assessment_id"] or response.mapping is None:
                raise ValueError()
        except (ServiceUnavailable, ValidationError, ValueError, TypeError, KeyError):
            deps.board.update("map", state="fail", summary="매핑 호출 또는 응답 검증 실패 · 가격 산정 불가",
                              detail={"error": "매핑 서비스 설정·연결·응답을 확인하세요"})
            raise ServiceUnavailable("매핑 호출 또는 응답 검증 실패") from None
        config = body["evidence"]["mapping"]
        detail = {"request_sha256": hashed, "assessment_id": response.assessment_id,
                  "source_provider": config["source_provider"], "target_provider": config["target_provider"],
                  "verdict": response.verdict, "reasons": list(response.reasons),
                  "mapping": response.mapping.model_dump(mode="json")}
        # Board.update atomically writes this run's JSON; a replay reuses the same result.
        deps.board.update("map", state="pass" if response.verdict == "pass" else "warn",
                          summary="클라우드 매핑·월 예상 비용 계산 완료" + (" (참고용)" if response.verdict == "pass" else " · 매핑 결과 확인 필요"),
                          detail=detail)
        return {"mapping_input_hash": hashed}
