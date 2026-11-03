"""그래프 노드 본문. 노드는 체크포인트를 저장한 뒤 다음으로 넘어가므로, 재개하면 끝나지 않은 노드만 처음부터 다시 돈다.

외부 호출을 시작하는 노드와 끝나기를 기다리는 노드를 나누고, 사람 확인 노드는 interrupt 앞에 부수 효과를 두지 않는다.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Annotated, Any, Protocol, TypedDict, TypeVar

from langgraph.types import interrupt

from delta_pipeline.clients import ServiceUnavailable
from delta_pipeline.migration import MigrationTools
from delta_pipeline.model import CANARY_STEPS, Board, Options, combined_verdict, dast_request, stage_states, summarize_result

DAST_POLL_SECONDS = 1
JENKINS_POLL_SECONDS = 2
DAST_DEADLINE_SECONDS = 600
JENKINS_DEADLINE_SECONDS = 1500
PREPARE_STAGES = ("준비", "판단 서비스 확인", "테스트", "SAST", "재발 확인", "이미지 빌드", "새 슬롯 배포")
RUNNING_JENKINS_STATES = ("IN_PROGRESS", "NOT_EXECUTED", "PAUSED_PENDING_INPUT", "QUEUED")
PROCEED = ("approve", "확인, 계속 진행")
REQUIRED_GATES = ("onprem", "dast")
PASSING_GATE_RECORDS = ("pass", "approved")
ELSEWHERE = "elsewhere"
READ_ATTEMPTS = 5  # Jenkins 는 빌드 중이나 재시작 직후 조회가 15초를 넘기기도 한다(빌드 #88)
LOG = logging.getLogger(__name__)
Step = Callable[["PipelineState"], dict[str, Any]]
T = TypeVar("T")


def _merge(left: dict[str, str] | None, right: dict[str, str] | None) -> dict[str, str]:
    return {**(left or {}), **(right or {})}


class PipelineState(TypedDict, total=False):
    halt: str | None
    gates: Annotated[dict[str, str], _merge]  # 게이트별 "pass" 또는 사람의 "approved"
    pending_gate: dict[str, Any] | None
    dast_scenario: str
    dast_run_id: str
    dast_target: str
    dast_passed: bool
    dast_before: dict[str, Any] | None
    phase: str
    build: int
    jenkins_status: str | None
    jenkins_input: dict[str, Any] | None
    jenkins_answer: str | None
    migrating: bool
    image: str
    shift_index: int
    rollback_reason: str | None


class Stopped(Exception):
    """사람이 중단했거나 판정·서비스 상태 때문에 멈췄다. 뒤 단계로 가지 않는다."""


class DeltaClient(Protocol):
    def assess(self, body: dict[str, Any]) -> dict[str, Any]: ...


class DastClient(Protocol):
    def config(self) -> dict[str, Any]: ...
    def start(self, scenario: str, planner: str, report: dict[str, Any]) -> str: ...
    def run(self, run_id: str) -> dict[str, Any]: ...


class JenkinsClient(Protocol):
    def trigger(self, scenario: str, run_key: str) -> int: ...
    def find_build(self, run_key: str) -> int | None: ...
    def describe(self, build: int) -> dict[str, Any]: ...
    def pending_inputs(self, build: int) -> list[dict[str, Any]]: ...
    def answer_input(self, build: int, input_id: str, *, approve: bool) -> None: ...
    def file(self, build: int, name: str) -> dict[str, Any] | None: ...
    def console_url(self, build: int) -> str: ...


@dataclass(frozen=True)
class Deps:
    run_id: str
    options: Options
    board: Board
    delta: DeltaClient
    dast: DastClient
    jenkins: JenkinsClient
    onprem_dir: Path
    service_ready: Callable[[], bool]
    on_release_start: Callable[[], None]
    sleep: Callable[[float], None]
    migration: MigrationTools | None = None


def guarded(step: Step) -> Step:
    """멈춤 사유를 상태에 남겨 그래프가 끝맺게 한다. interrupt 는 그대로 통과시킨다."""
    @wraps(step)
    def wrapper(state: PipelineState) -> dict[str, Any]:
        try:
            return step(state)
        except Stopped as stop:
            return {"halt": str(stop)}
        except ServiceUnavailable as exc:
            return {"halt": f"서비스 응답 없음, 통과로 보지 않는다: {exc}"}
    return wrapper


def assert_gates_passed(gates: Mapping[str, str]) -> None:
    """전이 표가 잘못돼도 판정 없이 배포를 시작하지 못하게 배포 직전에 다시 본다."""
    missing = [gate for gate in REQUIRED_GATES if gates.get(gate) not in PASSING_GATE_RECORDS]
    if missing:
        raise Stopped(f"통과 기록이 없어 배포하지 않는다: {', '.join(missing)}")


def sync_stages(deps: Deps, build: int, described: Mapping[str, Any]) -> None:
    board = deps.board
    stages = stage_states(described.get("stages", ()))
    board.add_detail("deploy-prepare", stages=[{"name": n, "status": stages.get(n, "NOT_STARTED")} for n in PREPARE_STAGES])
    if not stages.get("단계적 전환"):
        return  # 작업 폴더를 비우기 전이면 이전 빌드의 파일을 읽게 된다
    board.update("deploy-prepare", state="pass", summary="테스트·SAST·이미지·새 슬롯 완료")
    for percent in CANARY_STEPS:
        if board.state_of(f"canary-{percent}") != "pass":
            _sync_canary(deps, build, percent)
    if stages.get("단계적 전환") == "IN_PROGRESS":
        states = {p: board.state_of(f"canary-{p}") for p in CANARY_STEPS}
        next_step = next((p for p in CANARY_STEPS if states[p] == "pending"), None)
        if next_step and not any(s in ("running", "waiting") for s in states.values()):
            board.update(f"canary-{next_step}", state="running", summary="트래픽을 옮기고 측정하는 중")


def _sync_canary(deps: Deps, build: int, percent: int) -> None:
    node_id = f"canary-{percent}"
    metrics = deps.jenkins.file(build, f"metrics-{percent}.json")
    gate = deps.jenkins.file(build, f"gate-{percent}.json")
    if metrics:
        deps.board.add_detail(node_id, metrics=metrics.get("metrics", ()))
    if gate:
        deps.board.add_detail(node_id, results={"deployment_gate": summarize_result(gate)})
    if gate and gate.get("verdict") == "pass":
        deps.board.update(node_id, state="pass", summary="지표 통과, 다음 단계")
    elif gate:
        deps.board.update(node_id, state="running", summary=f"판정 {gate.get('verdict')}")
    elif metrics:
        deps.board.update(node_id, state="running", summary="지표 측정 완료, 판단 대기")


class Steps:
    def __init__(self, deps: Deps) -> None:
        self._deps = deps
        self._board = deps.board

    # ── 공통 게이트 ─────────────────────────────────────
    def _gate(self, key: str, node_id: str, results: Mapping[str, Mapping[str, Any]], *, what: str) -> dict[str, Any]:
        self._board.add_detail(node_id, results={k: summarize_result(v) for k, v in results.items()})
        if combined_verdict([r.get("verdict") for r in results.values()]) == "pass":
            self._board.update(node_id, state="pass", summary=f"{what}: 통과")
            return {"gates": {key: "pass"}, "pending_gate": None}
        count = sum(len(r.get("findings", ())) for r in results.values())
        self._board.update(node_id, summary=f"{what}: 사람 확인 필요 (항목 {count}건)")
        return {"pending_gate": {"key": key, "node_id": node_id, "what": what, "count": count}}

    def ask_gate(self, state: PipelineState) -> dict[str, Any]:
        gate = state["pending_gate"] or {}
        what = gate["what"]
        answer = interrupt({"node_id": gate["node_id"], "prompt": f"{what} 결과를 보고 다음 단계로 갈지 정합니다.",
                            "options": [PROCEED, ("stop", "중단")]})
        if answer != "approve":
            self._board.update(gate["node_id"], state="fail", summary=f"{what}: 사람이 중단")
            raise Stopped(f"{what}에서 사람이 중단했다")
        self._board.update(gate["node_id"], state="pass", summary=f"{what}: 사람 승인 후 진행 (항목 {gate['count']}건)")
        return {"gates": {gate["key"]: "approved"}, "pending_gate": None}

    # ── 온프레미스 ──────────────────────────────────────
    def _onprem_paths(self) -> dict[str, Path]:
        return {kind: self._deps.onprem_dir / f"{kind}.request.json" for kind in ("runtime_security", "migration_parity")}

    def onprem_evidence(self, _: PipelineState) -> dict[str, Any]:
        self._board.update("onprem-evidence", state="running", summary="저장된 실측 증거를 읽는 중")
        paths = self._onprem_paths()
        if not all(path.is_file() for path in paths.values()):
            self._board.update("onprem-evidence", state="fail", summary="수집된 증거가 없다")
            raise Stopped("온프레미스 증거가 없어 판단할 수 없다")
        evidence = json.loads(paths["runtime_security"].read_text(encoding="utf-8"))["evidence"]
        self._board.add_detail("onprem-evidence", collected_at=max(path.stat().st_mtime for path in paths.values()),
                               counts={"image_packages": len(evidence.get("image_vulnerabilities") or ()),
                                       "cluster_checks": len(evidence.get("cluster_checks") or ()),
                                       "log_signals": len(evidence.get("log_signals") or ())},
                               network_posture=evidence.get("network_posture"))
        self._board.update("onprem-evidence", state="pass", summary="VM 200 실측 증거 로드")
        return {"halt": None}

    def onprem_judge(self, _: PipelineState) -> dict[str, Any]:
        self._board.update("onprem-judge", state="running", summary="delta-ai 판단 중")
        results = {kind: self._onprem_result(kind, path) for kind, path in self._onprem_paths().items()}
        return self._gate("onprem", "onprem-judge", results, what="온프레미스 판단")

    def _onprem_result(self, kind: str, request_path: Path) -> dict[str, Any]:
        saved = request_path.with_name(f"{kind}.result.json")
        if (not self._deps.options.rejudge_onprem and saved.is_file()
                and saved.stat().st_mtime >= request_path.stat().st_mtime):
            self._board.add_detail("onprem-judge", cached=True)
            return json.loads(saved.read_text(encoding="utf-8"))
        result = self._deps.delta.assess(json.loads(request_path.read_text(encoding="utf-8")))
        saved.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self._board.add_detail("onprem-judge", cached=False)
        return result

    # ── DAST ────────────────────────────────────────────
    def dast_start(self, state: PipelineState) -> dict[str, Any]:
        scenario = state.get("dast_scenario") or self._deps.options.scenario.dast_scenario
        self._board.update("dast-run", state="running", summary=f"실제 브라우저로 점검 중 ({scenario})")
        config = self._deps.dast.config()
        run_id = self._deps.dast.start(scenario, self._deps.options.planner, config["sample_report"])
        self._board.add_detail("dast-run", run_id=run_id, scenario=scenario, planner=self._deps.options.planner)
        return {"dast_scenario": scenario, "dast_run_id": run_id,
                "dast_target": config.get("target_ids", ["local-fixture"])[0]}

    def dast_wait(self, state: PipelineState) -> dict[str, Any]:
        scenario, run_id = state["dast_scenario"], state["dast_run_id"]
        deadline = time.time() + DAST_DEADLINE_SECONDS
        while time.time() < deadline:
            run = self._read(lambda: self._deps.dast.run(run_id))
            if run.get("status") != "running":
                return self._show_dast_run(scenario, run)
            self._deps.sleep(DAST_POLL_SECONDS)
        raise ServiceUnavailable("DAST 점검이 제한 시간 안에 끝나지 않았다")

    def _show_dast_run(self, scenario: str, run: Mapping[str, Any]) -> dict[str, Any]:
        results = [{key: r.get(key) for key in ("finding_id", "title", "status", "message")}
                   | {"screenshots": (r.get("evidence") or {}).get("screenshots", [])} for r in run.get("results", ())]
        self._board.add_detail("dast-run", results=results)
        labels = ", ".join(sorted({str(r["status"]) for r in results})) or "결과 없음"
        # 관측 단계는 판정하지 않지만, 재현을 '통과'로 보여 주면 안 된다
        clean = bool(results) and all(r["status"] == "NOT_REPRODUCED" for r in results)
        self._board.update("dast-run", state="pass" if clean else "fail", summary=f"{scenario} 판 관측: {labels}")
        return {"halt": None}

    def dast_judge(self, state: PipelineState) -> dict[str, Any]:
        self._board.update("dast-judge", state="running", summary="delta-ai 판단 중")
        run_id, scenario = state["dast_run_id"], state["dast_scenario"]
        run = {**self._deps.dast.run(run_id), "run_id": run_id}
        result = self._deps.delta.assess(dast_request(run, assessment_id=f"dast-{run_id}", target=state["dast_target"]))
        if scenario != "fixed" and result.get("verdict") != "pass":
            summary = summarize_result(result)
            self._board.add_detail("dast-judge", results={"dast_verification": summary})
            self._board.update("dast-judge", summary="취약점 실측 재현: 배포를 멈췄다")
            return {"dast_before": summary, "pending_gate": None}
        return self._gate("dast", "dast-judge", {"dast_verification": result}, what="DAST 판단") | {"dast_before": None}

    def ask_reverify(self, state: PipelineState) -> dict[str, Any]:
        answer = interrupt({"node_id": "dast-judge", "prompt": "수정본을 다시 점검해 통과해야 배포로 넘어갑니다.",
                            "options": [("reverify", "수정본 재검증"), ("stop", "중단")]})
        if answer != "reverify":
            self._board.update("dast-judge", state="fail", summary="취약점이 남아 사람이 중단")
            raise Stopped("취약점이 재현돼 배포하지 않았다")
        self._board.add_detail("dast-judge", before=state["dast_before"])
        return {"dast_scenario": "fixed", "dast_before": None}

    # ── 배포 ────────────────────────────────────────────
    def deploy_check(self, _: PipelineState) -> dict[str, Any]:
        self._board.update("deploy-prepare", state="running", summary="Jenkins 빌드 시작")
        return {"phase": "release" if self._deps.service_ready() else "baseline"}

    def _read(self, call: Callable[[], T]) -> T:
        """조회는 다시 해도 안전하다. 한 번 실패했다고 멈추면 빌드만 계속 돌고 화면은 끝난 것처럼 보인다."""
        for attempt in range(READ_ATTEMPTS - 1):
            try:
                return call()
            except ServiceUnavailable:
                LOG.warning("조회 실패, 다시 시도 (%d/%d)", attempt + 1, READ_ATTEMPTS)
                self._deps.sleep(JENKINS_POLL_SECONDS)
        return call()

    def _ensure_build(self, scenario: str, run_key: str) -> int:
        jenkins = self._deps.jenkins
        found = self._read(lambda: jenkins.find_build(run_key))
        return found if found is not None else jenkins.trigger(scenario, run_key)

    def baseline_start(self, _: PipelineState) -> dict[str, Any]:
        """안정 슬롯이 없으면 나눌 트래픽이 없어 카나리를 보여 줄 수 없다. 기준 버전을 먼저 올린다."""
        self._board.update("deploy-prepare", summary="운영 중인 기준 버전이 없어 먼저 배포하는 중")
        return {"build": self._ensure_build("good", f"{self._deps.run_id}-baseline"), "jenkins_status": None}

    def baseline_done(self, state: PipelineState) -> dict[str, Any]:
        if state["jenkins_status"] != "SUCCESS":
            raise Stopped(f"기준 버전 배포 실패: {state['jenkins_status']}")
        return {"phase": "release", "jenkins_status": None}

    def release_start(self, state: PipelineState) -> dict[str, Any]:
        assert_gates_passed(state.get("gates") or {})
        self._deps.on_release_start()  # 기준 버전이 뜨기 전의 503 을 전환 실패로 세지 않는다
        build = self._ensure_build(self._deps.options.scenario.jenkins_scenario, f"{self._deps.run_id}-release")
        self._board.add_detail("deploy-prepare", build=build, url=self._deps.jenkins.console_url(build))
        return {"build": build, "phase": "release", "jenkins_status": None}

    def deploy_wait(self, state: PipelineState) -> dict[str, Any]:
        build, is_release = state["build"], state["phase"] == "release"
        deadline = time.time() + JENKINS_DEADLINE_SECONDS
        while time.time() < deadline:
            status, pending = self._read(lambda: self._deploy_tick(build, is_release=is_release))
            if status not in RUNNING_JENKINS_STATES:
                return {"jenkins_status": status, "jenkins_input": None}
            if pending:
                item = pending[0]
                return {"jenkins_input": {"id": str(item["id"]), "node_id": self._board.active_deploy_node(),
                                          "message": str(item.get("message", "Jenkins 가 사람 확인을 기다린다"))}}
            self._deps.sleep(JENKINS_POLL_SECONDS)
        raise ServiceUnavailable(f"Jenkins 빌드 #{build} 이 제한 시간 안에 끝나지 않았다. 빌드는 계속 돌 수 있다")

    def _deploy_tick(self, build: int, *, is_release: bool) -> tuple[str, list[dict[str, Any]]]:
        described = self._deps.jenkins.describe(build)
        if is_release:
            sync_stages(self._deps, build, described)
        status = str(described.get("status"))
        return status, self._deps.jenkins.pending_inputs(build) if status == "PAUSED_PENDING_INPUT" else []

    def ask_jenkins(self, state: PipelineState) -> dict[str, Any]:
        item = state["jenkins_input"] or {}
        answer = interrupt({"node_id": item["node_id"], "prompt": item["message"], "jenkins_build": state["build"],
                            "options": [PROCEED, ("stop", "중단·롤백")]})
        return {"jenkins_answer": answer}

    def jenkins_answer(self, state: PipelineState) -> dict[str, Any]:
        item, answer, build = state["jenkins_input"] or {}, state["jenkins_answer"], state["build"]
        # 재개로 다시 돌 때 이미 답한 입력에 또 답하면 오류가 난다
        pending = self._read(lambda: self._deps.jenkins.pending_inputs(build))
        if answer != ELSEWHERE and any(str(p.get("id")) == item["id"] for p in pending):
            self._deps.jenkins.answer_input(build, item["id"], approve=answer == "approve")
        self._board.update(item["node_id"], state="running")
        return {"jenkins_input": None, "jenkins_answer": None}

    def deploy_finish(self, state: PipelineState) -> dict[str, Any]:
        build, status = state["build"], str(state["jenkins_status"])
        if status == "SUCCESS":
            for node in ("deploy-prepare", *(f"canary-{p}" for p in CANARY_STEPS)):
                self._board.update(node, state="pass")
            if self._deps.options.scenario.migrate:
                return {"halt": None, "migrating": True}
            self._board.finish("deployed", f"무중단 승격 완료 (빌드 #{build})", passed=True)
            return {"halt": None}
        node_id = self._failed_node(build)
        analysis = self._read(lambda: self._deps.jenkins.file(build, "failure-analysis.json"))
        if analysis:
            self._board.add_detail(node_id, results={"deployment_gate": summarize_result(analysis)})
        self._board.update(node_id, state="fail", summary="기준 초과 또는 단계 실패: 자동 롤백" if node_id.startswith("canary-")
                           else f"배포 전 단계 실패 ({status})")
        self._board.finish("rolled_back", f"배포하지 않았다. 안정 버전 유지 (빌드 #{build}, {status})")
        return {"halt": None}

    def _failed_node(self, build: int) -> str:
        for percent in reversed(CANARY_STEPS):
            if self._read(lambda: self._deps.jenkins.file(build, f"metrics-{percent}.json")):  # noqa: B023
                return f"canary-{percent}"
        return "deploy-prepare"

    def halted(self, state: PipelineState) -> dict[str, Any]:
        self._board.finish("stopped", str(state.get("halt")))
        return {"halt": state.get("halt")}
