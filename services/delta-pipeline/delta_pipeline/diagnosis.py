"""멈춘 실행의 원인 진단(delta-ai deployment_gate). 연재님 delta-ai 연동.

판정은 파이프라인이 한다. 진단은 멈춘 뒤에 붙이는 설명(why)뿐이고 실행 결과를 바꾸지 않는다.
사람이 직접 중단한 실행은 진단하지 않는다. delta-ai 가 없거나 실패해도 실행에는 영향이 없다.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from typing import Any

from delta_pipeline.model import REPO_PREFIX, Board, Options

LOG = logging.getLogger(__name__)
LOG_TAIL_CHARS = 4000
HUMAN_STOP = "사람이 중단"
PROVIDERS = ("aws", "gcp", "onprem")

Assess = Callable[[dict[str, Any]], dict[str, Any]]


def failed_node(snapshot: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """처음으로 실패한 단계(결과 노드 제외). 없으면 결과 노드."""
    nodes = snapshot.get("nodes") or []
    failed = [n for n in nodes if n.get("state") == "fail" and n.get("id") != "result"]
    return failed[0] if failed else next((n for n in nodes if n.get("id") == "result"), None)


def request_for(run_id: str, options: Options, snapshot: Mapping[str, Any]) -> dict[str, Any] | None:
    """진단 요청. 사람이 중단했거나 실패 단계가 없으면 None."""
    if snapshot.get("outcome") != "stopped":
        return None
    node = failed_node(snapshot)
    result = next((n for n in snapshot.get("nodes") or [] if n.get("id") == "result"), {})
    reason = str(result.get("summary") or "")
    if node is None or HUMAN_STOP in reason or HUMAN_STOP in str(node.get("summary") or ""):
        return None
    detail = json.dumps(node.get("detail") or {}, ensure_ascii=False, sort_keys=True, default=str)
    log_tail = f"단계: {node.get('label') or node.get('id')}\n멈춘 사유: {reason}\n단계 요약: {node.get('summary') or ''}\n세부: {detail}"
    deployment: dict[str, Any] = {"stage": str(node.get("id")), "status": "failed", "log_tail": log_tail[-LOG_TAIL_CHARS:]}
    if options.staging_cloud in PROVIDERS:
        deployment["target"] = options.staging_cloud
    return {"assessment_id": f"{run_id}-halt", "kind": "deployment_gate",
            "service": {"service_id": options.repo.removeprefix(REPO_PREFIX) or options.repo, "repo": options.repo,
                        "commit": options.commit},
            "evidence": {"deployment": deployment}}


def summarize(result: Mapping[str, Any]) -> dict[str, Any]:
    """화면에 보일 것만 남긴다. delta-ai 의 verdict 는 쓰지 않는다(판정은 파이프라인)."""
    finding = next(iter(result.get("findings") or ()), {}) or {}
    meta = result.get("meta") or {}
    return {"status": "ready", "why": str(finding.get("why") or ""),
            "citations": list(finding.get("citations") or ()), "llm_used": bool(meta.get("llm_used"))}


def diagnose(board: Board, run_id: str, options: Options, assess: Assess) -> None:
    """멈춘 실행에 원인 설명을 붙인다. 실패하면 '진단 불가'만 남기고 조용히 끝난다."""
    request = request_for(run_id, options, board.snapshot())
    if request is None:
        return
    board.add_detail("result", diagnosis={"status": "running"})
    try:
        board.add_detail("result", diagnosis=summarize(assess(request)))
    except Exception as exc:  # noqa: BLE001 진단 실패는 실행 결과와 무관하다
        LOG.warning("실행 %s 원인 진단 실패: %s", run_id, type(exc).__name__)
        board.add_detail("result", diagnosis={"status": "unavailable"})
