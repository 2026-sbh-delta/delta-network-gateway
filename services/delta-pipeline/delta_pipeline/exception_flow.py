"""Graph-owned exception routing. Analysis output cannot choose a continuation or grant approval."""

from __future__ import annotations

import hashlib
import json
import re

from delta_ai.diagnostic_contract import (
    DiagnosisContext,
    DiagnosisReport,
    DiagnosisRequest,
    Observation,
    safe_text,
    unavailable_report,
    validate_report,
)
from langgraph.types import interrupt

from delta_pipeline.clients import ServiceUnavailable
from delta_pipeline.reports import ReportRef, ReportStore

HUMAN_GATES = {"ask_sast", "ask_dast"}
MAX_OBSERVATIONS = 20


def _bounded_summary(value, limit=1200):
    """Budget the serialized text, including escaping, rather than character count."""
    text = safe_text(str(value))
    budget, output = 0, []
    for char in text:
        size = len(json.dumps(char, ensure_ascii=False).encode("utf-8")) - 2
        if budget + size > limit:
            break
        output.append(char)
        budget += size
    return "".join(output) or "관측 내용 없음"


def prepare_exception(deps, name, result, target, state):
    """The original graph route, never an LLM, owns the destination after report review."""
    result = {
        k: safe_text(v)
        if k in ("halt", "rollback_reason", "terminal_summary") and isinstance(v, str)
        else v
        for k, v in result.items()
    }
    if result.get("failure_code"):
        result["halt"] = safe_text(str(result.get("halt", "")) + "; 오류 코드: " + str(result["failure_code"]))
    if name.startswith("ask_"):
        return result
    ambiguous = isinstance(target, str) and target in HUMAN_GATES
    failed = bool(
        result.get("halt")
        or result.get("rollback_reason")
        or result.get("terminal_outcome")
    )
    if not ambiguous and not failed:
        return result
    if (
        name == "migrate_rollback"
        and state.get("rollback_reason") == "쓰기 전환 전에 사람이 중단했다"
        and not result.get("halt")
    ):
        return result
    if name == "migrate_rollback" and state.get("diagnosis_pending"):
        # Keep the original error evidence; add the already performed safety action to the board separately.
        return result
    reason = (
        result.get("halt")
        or result.get("rollback_reason")
        or result.get("terminal_summary")
        or (state.get("pending_gate") or {}).get("what")
        or "규칙/에이전트 결과가 사람 확인을 요구한다"
    )
    if result.get("failure_type"):
        reason = f"{reason}; 오류 유형: {result['failure_type']}"
    observations = [
        {
            "evidence_id": "stage-result",
            "source": name,
            "summary": _bounded_summary(reason, 2400),
        }
    ]
    if name == "infrastructure_diff":
        detail = next(n["detail"] for n in deps.board.snapshot()["nodes"] if n["id"] == "map")
        if detail.get("verdict"):
            observations.append({"evidence_id": "diff-assessment", "source": name,
                "summary": f"판정 {detail['verdict']}, state serial {detail['state_serial']}, 매핑 행 {detail['row_count']}개"})
    for node in deps.board.snapshot()["nodes"]:
        if len(observations) >= MAX_OBSERVATIONS:
            break
        if node["state"] not in ("running", "waiting", "fail"):
            continue
        results = node.get("detail", {}).get("results") or {}
        # The team's DAST board uses a list of finding IDs/statuses, not raw evidence.
        if isinstance(results, list):
            for item in results:
                if len(observations) >= MAX_OBSERVATIONS:
                    break
                if not isinstance(item, dict):
                    continue
                observations.append(
                    {
                        "evidence_id": f"finding-{len(observations)}",
                        "source": node["id"],
                        "summary": _bounded_summary(
                            f"항목 {item.get('finding_id', '미확인')}: {item.get('status', '미확인')}"
                        ),
                    }
                )
            continue
        if not isinstance(results, dict):
            continue
        for item in results.values():
            if not isinstance(item, dict):
                continue
            for finding in item.get("findings", [])[:8]:
                rule = finding.get("rule_id") or ""
                rules = [rule] if re.fullmatch(r"[A-Z][A-Z0-9-]{0,63}", rule) else []
                observations.append(
                    {
                        "evidence_id": f"finding-{len(observations)}",
                        "source": name,
                        "summary": _bounded_summary(
                            finding.get("evidence")
                            or finding.get("title")
                            or "판단 근거 미확인"
                        ),
                        "rule_ids": rules,
                    }
                )
                if len(observations) >= MAX_OBSERVATIONS:
                    break
            if len(observations) >= MAX_OBSERVATIONS:
                break
        if len(observations) >= MAX_OBSERVATIONS:
            break
    # Keep code findings separate from the longer commit/image execution binding.
    nodes = {n["id"]: n for n in deps.board.snapshot()["nodes"]}
    evidence = nodes.get("dast", {}).get("detail", {}).get("diagnostic_evidence")
    findings = nodes.get("sast", {}).get("detail", {}).get("diagnostic_findings")
    for evidence_id, source, value in (
        ("scanner-execution-binding", "dast", evidence),
        ("scanner-source-findings", "sast", findings),
    ):
        if value:
            observations = observations[:MAX_OBSERVATIONS - 1] + [{
                "evidence_id": evidence_id, "source": source,
                "summary": _bounded_summary(json.dumps(value, ensure_ascii=False), 2400),
            }]
    if deps.options.execution_cloud == "onprem":
        stage = next((n["detail"] for n in deps.board.snapshot()["nodes"] if n["id"] == "stage"), {})
        facts = {k: stage[k] for k in ("platform_run_id", "execution_cloud", "platform_status",
            "platform_step", "error_code", "cleanup", "runtime_resource", "runtime_revision") if k in stage}
        if facts:
            # Only bounded provenance/status fields; no arbitrary provider logs or settings.
            observations = observations[:MAX_OBSERVATIONS - 1] + [{"evidence_id": "onprem-runtime",
                "source": "platform-worker", "summary": _bounded_summary(json.dumps(facts, ensure_ascii=False), 2400)}]
    if state.get("recovery_events"):
        observations = observations[:MAX_OBSERVATIONS - 1] + [{
            "evidence_id": "bounded-recovery", "source": "pipeline-recovery",
            "summary": _bounded_summary(json.dumps(state["recovery_events"], ensure_ascii=False), 2400)}]
    count = state.get("diagnosis_count", 0) + 1
    service = deps.options.repo.rsplit("/", 1)[-1]
    if not isinstance(service, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", service
    ):
        service = "unknown-service"
    case = (
        "case-"
        + hashlib.sha256(
            json.dumps(
                [deps.run_id, name, count, observations], sort_keys=True
            ).encode()
        ).hexdigest()[:32]
    )
    request = DiagnosisRequest(
        case_id=case,
        run_id=deps.run_id,
        service_id=service,
        node_id=name,
        trigger="ambiguous_result" if ambiguous else "execution_failure",
        observations=observations,
        context=DiagnosisContext(environment="onprem" if deps.options.execution_cloud == "onprem" else deps.options.staging_cloud,
            error_code=result.get("failure_code") or state.get("failure_code") or result.get("failure_type")
                or ("scanner_requires_review" if ambiguous else "execution_failed"),
            commit=state.get("commit") or deps.options.commit,
            image_digest=state.get("image_digest")),
    )
    resume = "exception_finish" if target in ("__end__", "migrate_rollback") else target
    return {
        **result,
        "diagnosis_pending": True,
        "diagnosis_count": count,
        "diagnosis_request": request.model_dump(mode="json"),
        "diagnosis_resume": resume,
        "diagnosis_report": None,
        "diagnosis_ref": None,
    }


def record_review(deps, state, answer):
    if state.get("diagnosis_ref"):
        ReportStore(deps.report_dir).decide(
            ReportRef.model_validate(state["diagnosis_ref"]), answer
        )
        if answer in ("stop", "acknowledge"):
            from delta_pipeline.case_learning import CaseLearning
            CaseLearning(deps).record(state["diagnosis_ref"], sequence=1,
                                      outcome="stopped" if answer == "stop" else "unknown")


class ExceptionFlow:
    def __init__(self, deps):
        self.deps = deps
        self.store = ReportStore(deps.report_dir)

    def analyze(self, state):
        request = DiagnosisRequest.model_validate(state["diagnosis_request"])
        if (state.get("halt") and request.node_id != "infrastructure_diff" and state.get("diff_started")
                and not state.get("mapping_input_hash") and self.deps.diff):
            request = self._parallel_diff_evidence(request)
        if self.deps.evidence is not None:
            request = self.deps.evidence.enrich(request, started_at=self.deps.board.snapshot().get("started_at"))
        self.deps.board.update(
            "exception-analysis",
            state="running",
            summary="delta-ai에 원인 후보와 근거 분석 요청",
        )
        report = unavailable_report(request)
        if self.deps.diagnostic is not None:
            try:
                candidate = self.deps.diagnostic.diagnose(
                    request.model_dump(mode="json")
                )
                report = validate_report(
                    DiagnosisReport.model_validate(candidate), request
                )
            except Exception:  # noqa: BLE001 — diagnostic outages must produce a reviewable fallback
                # An unavailable/malformed diagnosis is itself a report limitation, never another diagnosis loop.
                report = unavailable_report(request)
        self.deps.board.update(
            "exception-analysis",
            state="pass" if report.status == "analyzed" else "fail",
            summary=report.summary,
        )
        return {"diagnosis_request": request.model_dump(mode="json"), "diagnosis_report": report.model_dump(mode="json")}

    def _parallel_diff_evidence(self, request: DiagnosisRequest) -> DiagnosisRequest:
        # At this point the failed web branch has released its staging lease.
        try:
            result = self.deps.diff.result()
        except ServiceUnavailable as exc:
            reason = _bounded_summary(exc)
        else:
            if result.assessment.verdict != "fix":
                self.deps.board.update("map", state="skipped", summary="웹 단계 실패로 차분 확인 단계에 도달하지 못했습니다")
                return request
            reason = "워크로드 차분에서 수정이 필요한 항목 발견"
        self.deps.board.update("map", state="fail", summary=reason)
        observation = Observation(evidence_id="parallel-diff", source="infrastructure_diff", summary=reason)
        return request.model_copy(update={"observations": (*request.observations[:MAX_OBSERVATIONS - 1], observation)})

    def save(self, state):
        self.deps.board.update(
            "exception-report", state="running", summary="분석 보고서를 저장하는 중"
        )
        try:
            request = DiagnosisRequest.model_validate(state["diagnosis_request"])
            report = DiagnosisReport.model_validate(state["diagnosis_report"])
            ref = self.store.save(request, report)
            self.store.verify(ref)
        except Exception:  # noqa: BLE001 — any persistence/validation failure must halt execution
            self.deps.board.update(
                "exception-report",
                state="fail",
                summary="보고서 저장·검증 실패. 후속 실행을 중단한다",
            )
            return {
                "halt": "보고서 저장·검증 실패로 진행하지 않았다",
                "report_failed": True,
                "diagnosis_pending": False,
            }
        data = {
            "reference": ref.model_dump(),
            "summary": report.summary,
            "status": report.status,
            "next_checks": list(report.next_checks),
            "hypotheses": [h.model_dump(mode="json") for h in report.hypotheses],
            "limitations": list(report.limitations),
            "model": report.model,
            "observations": [o.model_dump(mode="json") for o in request.observations],
            "related_cases": [c.model_dump(mode="json") for c in report.related_cases],
        }
        from delta_pipeline.case_learning import CaseLearning
        CaseLearning(self.deps).record(ref)
        prior=next(n for n in self.deps.board.snapshot()['nodes'] if n['id']=='exception-report').get('detail',{}).get('report')
        if prior and prior.get('reference',{}).get('case_id')!=ref.case_id:
            self.deps.board.archive('exception-report')
            self.deps.board.add_detail('exception-review',recovery_plan=None)
        self.deps.board.add_detail("exception-report", report=data)
        self.deps.board.add_detail("exception-review", diagnostic_report=data)
        self.deps.board.update(
            "exception-report",
            state="pass",
            summary=f"JSON·Markdown 보고서 저장 완료: {ref.case_id}",
        )
        if state["diagnosis_resume"] in HUMAN_GATES:
            node_id = (state.get("pending_gate") or {}).get("node_id", "dast")
            self.deps.board.add_detail(node_id, diagnostic_report=data)
        elif request.node_id == "infrastructure_diff" or any(o.evidence_id == "parallel-diff" for o in request.observations):
            self.deps.board.add_detail("map", diagnostic_report=data)
        return {
            "diagnosis_ref": ref.model_dump(),
            "diagnosis_pending": False,
            "report_failed": False,
        }

    def review_failure(self, state):
        # Verification happens before asking; the only effects after interrupt are the audited human decision.
        ref = ReportRef.model_validate(state["diagnosis_ref"])
        self.store.verify(ref)
        answer = interrupt(
            {
                "node_id": "exception-review",
                "prompt": "보고서를 확인합니다. 실패한 작업은 자동 재실행하지 않습니다.",
                "options": [("acknowledge", "보고서 확인·실행 종료"), ("stop", "중단")],
                "report": ref.model_dump(),
            }
        )
        self.store.decide(ref, answer)
        from delta_pipeline.case_learning import CaseLearning
        CaseLearning(self.deps).record(ref, sequence=1, outcome="stopped" if answer == "stop" else "unknown")
        self.deps.board.update(
            "exception-review",
            state="pass",
            summary="보고서 확인 완료. 실패한 작업을 재실행하지 않는다",
        )
        return {"diagnosis_pending": False}

    def finish(self, state):
        outcome = state.get("terminal_outcome") or "stopped"
        summary = (
            state.get("terminal_summary")
            or state.get("halt")
            or "실패한 실행을 종료했다"
        )
        self.deps.board.finish(outcome, summary)
        return {}
