"""시연 한 번의 흐름: 온프레미스 증거 → DAST → 판단 → 카나리 배포.

증거는 각 서비스가 만들고 판정은 delta-ai 만 내린다. 이 모듈은 순서를 잇고 판정대로 멈추거나 진행한다.
어느 서비스든 응답이 없으면 통과로 보지 않고 멈춘다.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from clients import ServiceUnavailable

DAST_POLL_SECONDS = 1
JENKINS_POLL_SECONDS = 2
DAST_DEADLINE_SECONDS = 600
JENKINS_DEADLINE_SECONDS = 1500
DECISION_DEADLINE_SECONDS = 1800
MAX_FINDINGS_SHOWN = 40
CANARY_STEPS = (10, 50, 100)
PREPARE_STAGES = ("준비", "판단 서비스 확인", "테스트", "SAST", "재발 확인", "이미지 빌드", "새 슬롯 배포")
RUNNING_JENKINS_STATES = ("IN_PROGRESS", "NOT_EXECUTED", "PAUSED_PENDING_INPUT", "QUEUED")
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
STATUS_ORDER = {"fail": 0, "unknown": 1, "dismissed": 2}


@dataclass(frozen=True)
class Scenario:
    id: str
    label: str
    description: str
    dast_scenario: str
    jenkins_scenario: str


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("normal", "정상 릴리스", "DAST 통과 후 10→50→100% 무중단 승격", "fixed", "good"),
    Scenario("vulnerable", "취약한 릴리스", "XSS 실측 재현으로 배포 전에 멈추고, 수정본 재검증 후 진행", "vulnerable", "good"),
    Scenario("bad_canary", "장애 릴리스", "새 버전이 500 을 내 10%에서 자동 롤백하고 AI 가 원인을 설명", "fixed", "bad-canary"),
)


@dataclass
class Node:
    id: str
    label: str
    owner: str
    state: str = "pending"  # pending·running·pass·waiting·fail·skipped
    summary: str = ""
    started_at: float | None = None
    ended_at: float | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    decision: dict[str, Any] | None = None


def initial_nodes() -> list[Node]:
    return [
        Node("onprem-evidence", "온프레미스 증거", "수집기 · VM 200"),
        Node("onprem-judge", "이전·보안 판단", "delta-ai"),
        Node("dast-run", "DAST 실측", "delta-dast · Chrome"),
        Node("dast-judge", "DAST 판단", "delta-ai"),
        Node("deploy-prepare", "빌드·SAST·슬롯 배포", "Jenkins"),
        *(Node(f"canary-{p}", f"트래픽 {p}%", "Jenkins + delta-ai") for p in CANARY_STEPS),
        Node("result", "결과", "시연 콘솔"),
    ]


def summarize_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """화면에 필요한 값만 남긴다. 문구는 그대로 두고 화면이 글자로만 그린다."""
    findings = sorted(result.get("findings", ()),
                      key=lambda f: (STATUS_ORDER.get(f.get("status"), 9), SEVERITY_ORDER.get(f.get("severity"), 9)))
    meta = result.get("meta") or {}
    return {
        "verdict": result.get("verdict"), "reasons": list(result.get("reasons", ())),
        "findings": [{key: f.get(key) for key in ("rule_id", "title", "severity", "status", "why", "evidence")}
                     for f in findings[:MAX_FINDINGS_SHOWN]],
        "finding_count": len(findings),
        "checks": list(result.get("checks", ())), "verified_controls": list(result.get("verified_controls", ())),
        "posture": result.get("posture"),
        "llm": {"used": bool(meta.get("llm_used")), "model": meta.get("model"), "duration_ms": meta.get("duration_ms")},
    }


def combined_verdict(verdicts: Sequence[str | None]) -> str:
    """하나라도 막으면 막는다. 알 수 없는 값은 통과로 보지 않는다."""
    return "pass" if verdicts and all(v == "pass" for v in verdicts) else "needs_human"


def dast_request(run: Mapping[str, Any], *, assessment_id: str, target: str) -> dict[str, Any]:
    results = [{"finding_id": r["finding_id"], "title": r.get("title", ""), "status": r["status"],
                "message": r.get("message", "")} for r in run.get("results", ())]
    return {"assessment_id": assessment_id, "kind": "dast_verification", "service": {"service_id": "board"},
            "evidence": {"dast": {"run_id": run["run_id"], "target": target, "results": results}}}


def stage_states(stages: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    return {str(s.get("name")): str(s.get("status")) for s in stages}


class DeltaClient(Protocol):
    def assess(self, body: dict[str, Any]) -> dict[str, Any]: ...


class DastClient(Protocol):
    def config(self) -> dict[str, Any]: ...
    def start(self, scenario: str, planner: str, report: dict[str, Any]) -> str: ...
    def run(self, run_id: str) -> dict[str, Any]: ...


class JenkinsClient(Protocol):
    def trigger(self, scenario: str) -> int: ...
    def describe(self, build: int) -> dict[str, Any]: ...
    def pending_inputs(self, build: int) -> list[dict[str, Any]]: ...
    def answer_input(self, build: int, input_id: str, *, approve: bool) -> None: ...
    def file(self, build: int, name: str) -> dict[str, Any] | None: ...
    def console_url(self, build: int) -> str: ...


class Stopped(Exception):
    """사람이 중단했거나 판정·서비스 상태 때문에 멈췄다. 뒤 단계로 가지 않는다."""


@dataclass(frozen=True)
class Options:
    scenario: Scenario
    planner: str = "reviewed"
    rejudge_onprem: bool = False


class Run:
    def __init__(self, options: Options, *, delta: DeltaClient, dast: DastClient, jenkins: JenkinsClient,
                 onprem_dir: Path, service_ready: Callable[[], bool], on_release_start: Callable[[], None] = lambda: None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.options = options
        self._delta, self._dast, self._jenkins = delta, dast, jenkins
        self._onprem_dir, self._service_ready, self._sleep = onprem_dir, service_ready, sleep
        self._on_release_start = on_release_start
        self._lock = threading.Lock()
        self._nodes = {node.id: node for node in initial_nodes()}
        self._outcome: str | None = None
        self._started_at = time.time()
        self._answer: str | None = None
        self._answered = threading.Event()
        self._stop_requested = False

    # ── 상태 ──────────────────────────────────────────
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"scenario": asdict(self.options.scenario), "planner": self.options.planner,
                    "outcome": self._outcome, "started_at": self._started_at,
                    "nodes": [deepcopy(asdict(node)) for node in self._nodes.values()]}

    def _update(self, node_id: str, **fields: Any) -> None:
        with self._lock:
            node = self._nodes[node_id]
            for key, value in fields.items():
                setattr(node, key, value)
            if fields.get("state") == "running" and node.started_at is None:
                node.started_at = time.time()
            if fields.get("state") in ("pass", "fail", "skipped"):
                node.ended_at = time.time()

    def _detail(self, node_id: str, **values: Any) -> None:
        with self._lock:
            self._nodes[node_id].detail = {**self._nodes[node_id].detail, **values}

    def answer(self, value: str) -> bool:
        with self._lock:
            waiting = next((n for n in self._nodes.values() if n.decision), None)
            if waiting is None or value not in {o["value"] for o in waiting.decision["options"]}:
                return False
        self._answer = value
        self._answered.set()
        return True

    def _ask(self, node_id: str, prompt: str, options: Sequence[tuple[str, str]], *, poll: Callable[[], str | None] | None = None) -> str:
        """사람의 답을 기다린다. poll 이 값을 돌려주면 그 값으로 끝낸다. 답이 없으면 진행하지 않는다."""
        self._answered.clear()
        self._update(node_id, state="waiting", decision={
            "prompt": prompt, "options": [{"value": v, "label": label} for v, label in options]})
        deadline = time.time() + DECISION_DEADLINE_SECONDS
        try:
            while time.time() < deadline:
                if self._answered.wait(timeout=0.5):
                    return self._answer or "stop"
                if poll is not None and (polled := poll()) is not None:
                    return polled
            raise Stopped("사람 확인 시간이 지났다")
        finally:
            self._update(node_id, decision=None)

    # ── 실행 ──────────────────────────────────────────
    def execute(self) -> None:
        try:
            self._onprem()
            self._dast_step()
            self._deploy()
        except Stopped as stop:
            self._finish("stopped", str(stop))
        except ServiceUnavailable as exc:
            self._finish("stopped", f"서비스 응답 없음, 통과로 보지 않는다: {exc}")

    def _finish(self, outcome: str, summary: str, *, passed: bool = False) -> None:
        with self._lock:
            pending = [n for n in self._nodes.values() if n.state == "pending" and n.id != "result"]
            for node in pending:
                node.state = "skipped"
            for node in self._nodes.values():
                if node.state in ("running", "waiting"):
                    node.state, node.ended_at, node.decision = "fail", time.time(), None
            self._outcome = outcome
        self._update("result", state="pass" if passed else "fail", summary=summary)

    def _judge_gate(self, node_id: str, results: Mapping[str, Mapping[str, Any]], *, what: str) -> None:
        self._detail(node_id, results={k: summarize_result(v) for k, v in results.items()})
        verdict = combined_verdict([r.get("verdict") for r in results.values()])
        if verdict == "pass":
            self._update(node_id, state="pass", summary=f"{what}: 통과")
            return
        count = sum(len(r.get("findings", ())) for r in results.values())
        self._update(node_id, summary=f"{what}: 사람 확인 필요 (항목 {count}건)")
        answer = self._ask(node_id, f"{what} 결과를 보고 다음 단계로 갈지 정합니다.",
                           (("approve", "확인, 계속 진행"), ("stop", "중단")))
        if answer != "approve":
            self._update(node_id, state="fail", summary=f"{what}: 사람이 중단")
            raise Stopped(f"{what}에서 사람이 중단했다")
        self._update(node_id, state="pass", summary=f"{what}: 사람 승인 후 진행 (항목 {count}건)")

    def _onprem(self) -> None:
        self._update("onprem-evidence", state="running", summary="저장된 실측 증거를 읽는 중")
        kinds = ("runtime_security", "migration_parity")
        paths = {kind: self._onprem_dir / f"{kind}.request.json" for kind in kinds}
        if not all(path.is_file() for path in paths.values()):
            self._update("onprem-evidence", state="fail", summary="수집된 증거가 없다")
            raise Stopped("온프레미스 증거가 없어 판단할 수 없다")
        collected_at = max(path.stat().st_mtime for path in paths.values())
        requests = {kind: json.loads(path.read_text(encoding="utf-8")) for kind, path in paths.items()}
        evidence = requests["runtime_security"]["evidence"]
        self._detail("onprem-evidence", collected_at=collected_at,
                     counts={"image_packages": len(evidence.get("image_vulnerabilities") or ()),
                             "cluster_checks": len(evidence.get("cluster_checks") or ()),
                             "log_signals": len(evidence.get("log_signals") or ())},
                     network_posture=evidence.get("network_posture"))
        self._update("onprem-evidence", state="pass", summary="VM 200 실측 증거 로드")

        self._update("onprem-judge", state="running", summary="delta-ai 판단 중")
        results = {kind: self._onprem_result(kind, requests[kind], paths[kind]) for kind in kinds}
        self._judge_gate("onprem-judge", results, what="온프레미스 판단")

    def _onprem_result(self, kind: str, request: dict[str, Any], request_path: Path) -> dict[str, Any]:
        saved = request_path.with_name(f"{kind}.result.json")
        if (not self.options.rejudge_onprem and saved.is_file()
                and saved.stat().st_mtime >= request_path.stat().st_mtime):
            self._detail("onprem-judge", cached=True)
            return json.loads(saved.read_text(encoding="utf-8"))
        result = self._delta.assess(request)
        saved.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self._detail("onprem-judge", cached=False)
        return result

    def _run_dast(self, scenario: str) -> dict[str, Any]:
        config = self._dast.config()
        run_id = self._dast.start(scenario, self.options.planner, config["sample_report"])
        self._detail("dast-run", run_id=run_id, scenario=scenario, planner=self.options.planner)
        deadline = time.time() + DAST_DEADLINE_SECONDS
        while time.time() < deadline:
            run = self._dast.run(run_id)
            if run.get("status") != "running":
                return {**run, "run_id": run_id, "target": config.get("target_ids", ["local-fixture"])[0]}
            self._sleep(DAST_POLL_SECONDS)
        raise ServiceUnavailable("DAST 점검이 제한 시간 안에 끝나지 않았다")

    def _dast_once(self, scenario: str) -> dict[str, Any]:
        self._update("dast-run", state="running", summary=f"실제 브라우저로 점검 중 ({scenario})")
        run = self._run_dast(scenario)
        results = [{key: r.get(key) for key in ("finding_id", "title", "status", "message")}
                   | {"screenshots": (r.get("evidence") or {}).get("screenshots", [])} for r in run.get("results", ())]
        self._detail("dast-run", results=results)
        labels = ", ".join(sorted({str(r["status"]) for r in results})) or "결과 없음"
        # 관측 단계는 판정하지 않지만, 재현을 '통과'로 보여 주면 안 된다
        clean = bool(results) and all(r["status"] == "NOT_REPRODUCED" for r in results)
        self._update("dast-run", state="pass" if clean else "fail", summary=f"{scenario} 판 관측: {labels}")
        self._update("dast-judge", state="running", summary="delta-ai 판단 중")
        return self._delta.assess(dast_request(run, assessment_id=f"dast-{run['run_id']}", target=run["target"]))

    def _dast_step(self) -> None:
        scenario = self.options.scenario.dast_scenario
        result = self._dast_once(scenario)
        if scenario != "fixed" and result.get("verdict") != "pass":
            self._detail("dast-judge", results={"dast_verification": summarize_result(result)})
            self._update("dast-judge", summary="취약점 실측 재현: 배포를 멈췄다")
            answer = self._ask("dast-judge", "수정본을 다시 점검해 통과해야 배포로 넘어갑니다.",
                               (("reverify", "수정본 재검증"), ("stop", "중단")))
            if answer != "reverify":
                self._update("dast-judge", state="fail", summary="취약점이 남아 사람이 중단")
                raise Stopped("취약점이 재현돼 배포하지 않았다")
            self._detail("dast-judge", before=summarize_result(result))
            result = self._dast_once("fixed")
        self._judge_gate("dast-judge", {"dast_verification": result}, what="DAST 판단")

    # ── 배포 ──────────────────────────────────────────
    def _deploy(self) -> None:
        self._update("deploy-prepare", state="running", summary="Jenkins 빌드 시작")
        if not self._service_ready():
            self._baseline()
        self._on_release_start()  # 기준 버전이 뜨기 전의 503 을 전환 실패로 세지 않는다
        build = self._jenkins.trigger(self.options.scenario.jenkins_scenario)
        self._detail("deploy-prepare", build=build, url=self._jenkins.console_url(build))
        deadline = time.time() + JENKINS_DEADLINE_SECONDS
        while time.time() < deadline:
            described = self._jenkins.describe(build)
            self._sync_stages(build, described)
            status = str(described.get("status"))
            if status not in RUNNING_JENKINS_STATES:
                self._finish_deploy(build, status)
                return
            if status == "PAUSED_PENDING_INPUT":
                self._answer_jenkins_input(build)
            self._sleep(JENKINS_POLL_SECONDS)
        raise ServiceUnavailable("Jenkins 빌드가 제한 시간 안에 끝나지 않았다")

    def _baseline(self) -> None:
        """안정 슬롯이 없으면 나눌 트래픽이 없어 카나리를 보여 줄 수 없다. 기준 버전을 먼저 올린다."""
        self._update("deploy-prepare", summary="운영 중인 기준 버전이 없어 먼저 배포하는 중")
        build = self._jenkins.trigger("good")
        deadline = time.time() + JENKINS_DEADLINE_SECONDS
        while time.time() < deadline:
            status = str(self._jenkins.describe(build).get("status"))
            if status not in RUNNING_JENKINS_STATES:
                if status != "SUCCESS":
                    raise Stopped(f"기준 버전 배포 실패: {status}")
                return
            if status == "PAUSED_PENDING_INPUT":
                self._answer_jenkins_input(build)
            self._sleep(JENKINS_POLL_SECONDS)
        raise ServiceUnavailable("기준 버전 배포가 끝나지 않았다")

    def _sync_stages(self, build: int, described: Mapping[str, Any]) -> None:
        stages = stage_states(described.get("stages", ()))
        self._detail("deploy-prepare", stages=[{"name": n, "status": stages.get(n, "NOT_STARTED")} for n in PREPARE_STAGES])
        if stages.get("단계적 전환"):
            self._update("deploy-prepare", state="pass", summary="테스트·SAST·이미지·새 슬롯 완료")
        if not stages.get("단계적 전환"):
            return  # 작업 폴더를 비우기 전이면 이전 빌드의 파일을 읽게 된다
        for percent in CANARY_STEPS:
            if self._node_state(f"canary-{percent}") != "pass":
                self._sync_canary(build, percent)
        if stages.get("단계적 전환") == "IN_PROGRESS":
            next_step = next((f"canary-{p}" for p in CANARY_STEPS if self._node_state(f"canary-{p}") == "pending"), None)
            if next_step and not self._any_running_canary():
                self._update(next_step, state="running", summary="트래픽을 옮기고 측정하는 중")

    def _node_state(self, node_id: str) -> str:
        with self._lock:
            return self._nodes[node_id].state

    def _any_running_canary(self) -> bool:
        return any(self._node_state(f"canary-{p}") in ("running", "waiting") for p in CANARY_STEPS)

    def _sync_canary(self, build: int, percent: int) -> None:
        node_id = f"canary-{percent}"
        metrics = self._jenkins.file(build, f"metrics-{percent}.json")
        gate = self._jenkins.file(build, f"gate-{percent}.json")
        if metrics:
            self._detail(node_id, metrics=metrics.get("metrics", ()))
        if gate:
            self._detail(node_id, results={"deployment_gate": summarize_result(gate)})
        if gate and gate.get("verdict") == "pass":
            self._update(node_id, state="pass", summary="지표 통과, 다음 단계")
        elif gate:
            self._update(node_id, state="running", summary=f"판정 {gate.get('verdict')}")
        elif metrics:
            self._update(node_id, state="running", summary="지표 측정 완료, 판단 대기")

    def _answer_jenkins_input(self, build: int) -> None:
        pending = self._jenkins.pending_inputs(build)
        if not pending:
            return
        item = pending[0]
        node_id = self._active_deploy_node()

        def resolved_elsewhere() -> str | None:
            # Jenkins 화면에서 직접 답한 경우 콘솔이 대기를 풀어야 한다
            return "elsewhere" if not self._jenkins.pending_inputs(build) else None

        answer = self._ask(node_id, str(item.get("message", "Jenkins 가 사람 확인을 기다린다")),
                           (("approve", "확인, 계속 진행"), ("stop", "중단·롤백")), poll=resolved_elsewhere)
        if answer != "elsewhere":
            self._jenkins.answer_input(build, str(item["id"]), approve=answer == "approve")
        self._update(node_id, state="running")

    def _active_deploy_node(self) -> str:
        with self._lock:
            started = [n.id for n in self._nodes.values() if n.id.startswith("canary-") and n.state in ("running", "waiting")]
        return started[-1] if started else "deploy-prepare"

    def _finish_deploy(self, build: int, status: str) -> None:
        analysis = self._jenkins.file(build, "failure-analysis.json")
        if status == "SUCCESS":
            for node in ("deploy-prepare", *(f"canary-{p}" for p in CANARY_STEPS)):
                self._update(node, state="pass")
            self._finish("deployed", f"무중단 승격 완료 (빌드 #{build})", passed=True)
            return
        node_id = self._failed_node(build)
        if analysis:
            self._detail(node_id, results={"deployment_gate": summarize_result(analysis)})
        self._update(node_id, state="fail", summary="기준 초과 또는 단계 실패: 자동 롤백" if node_id.startswith("canary-")
                     else f"배포 전 단계 실패 ({status})")
        self._finish("rolled_back", f"배포하지 않았다. 안정 버전 유지 (빌드 #{build}, {status})")

    def _failed_node(self, build: int) -> str:
        for percent in reversed(CANARY_STEPS):
            if self._jenkins.file(build, f"metrics-{percent}.json"):
                return f"canary-{percent}"
        return "deploy-prepare"
