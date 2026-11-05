"""그래프 노드 본문. 노드는 체크포인트를 저장한 뒤 다음으로 넘어가므로, 재개하면 끝나지 않은 노드만 처음부터 다시 돈다.

외부 호출을 시작하는 노드와 끝나기를 기다리는 노드를 나누고, 사람 확인 노드는 interrupt 앞에 부수 효과를 두지 않는다.
게이트 판정은 여기 코드가 한다(명세 v1.1 7장). LLM 은 통과를 정하지 않는다.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import wraps
from typing import Annotated, Any, Protocol, TypedDict, TypeVar
from uuid import NAMESPACE_URL, uuid5

from langgraph.types import interrupt

from delta_pipeline.clients import ServiceUnavailable, evidence_hash
from delta_pipeline.model import REPO_PREFIX, Board, Options

POLL_SECONDS = 3
JENKINS_POLL_SECONDS = 2
BUILD_DEADLINE_SECONDS = 1200
STAGE_DEADLINE_SECONDS = 900
SAST_DEADLINE_SECONDS = 1200
DAST_DEADLINE_SECONDS = 1800
RUNNING_JENKINS_STATES = ("IN_PROGRESS", "NOT_EXECUTED", "PAUSED_PENDING_INPUT", "QUEUED")
PROCEED = ("approve", "확인, 계속 진행")
PASSING_GATE_RECORDS = ("pass", "approved")
DAST_PASS = ("NOT_REPRODUCED",)
CLOUD = "gcp"  # GCP 스테이징을 먼저 완주한다. AWS 는 같은 계약을 적용한 뒤 붙인다
READ_ATTEMPTS = 5  # Jenkins 는 빌드 중이나 재시작 직후 조회가 15초를 넘기기도 한다(빌드 #88)
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
ROWS_RE = re.compile(r"남은 행 (\d+)")
REPO_RE = re.compile(re.escape(REPO_PREFIX) + r"[A-Za-z0-9._-]+")
LOG = logging.getLogger(__name__)
Step = Callable[["PipelineState"], dict[str, Any]]
T = TypeVar("T")


def _merge(left: dict[str, str] | None, right: dict[str, str] | None) -> dict[str, str]:
    return {**(left or {}), **(right or {})}


def _first(left: str | None, right: str | None) -> str | None:
    """빌드·SAST 가 동시에 멈춰도 쓰기 충돌 없이 먼저 남은 사유를 지킨다."""
    return left or right


class PipelineState(TypedDict, total=False):
    halt: Annotated[str | None, _first]
    gates: Annotated[dict[str, str], _merge]  # 게이트별 "pass" 또는 사람의 "approved"
    pending_gate: dict[str, Any] | None
    build: int
    image: str
    image_digest: str
    sast_run_id: str
    dast_targets: int
    deploy_build: int
    staging_url: str
    staging_revision: str | None
    dast_run_id: str | None


class Stopped(Exception):
    """사람이 중단했거나 판정·서비스 상태 때문에 멈췄다. 뒤 단계로 가지 않는다."""


class SastClient(Protocol):
    def start(self, run_id: str, repo: str, commit: str) -> dict[str, Any]: ...
    def scan(self, run_id: str) -> dict[str, Any]: ...
    def sarif(self, run_id: str) -> dict[str, Any]: ...


class DastClient(Protocol):
    def submit(self, run_id: str, binding: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]: ...
    def view(self, run_id: str) -> dict[str, Any]: ...


class StagingClient(Protocol):
    def release(self, cloud: str, holder: str) -> bool: ...


class JenkinsClient(Protocol):
    def trigger(self, params: Mapping[str, str], run_key: str) -> int: ...
    def find_build(self, run_key: str) -> int | None: ...
    def status(self, build: int) -> str: ...
    def file(self, build: int, name: str) -> dict[str, Any] | None: ...
    def console_url(self, build: int) -> str: ...


@dataclass(frozen=True)
class Deps:
    run_id: str
    options: Options
    board: Board
    sast: SastClient
    dast: DastClient
    build_jenkins: JenkinsClient   # delta-build: 빌드 에이전트
    deploy_jenkins: JenkinsClient  # delta-deploy: 컨트롤러 배포 전용 실행기
    staging: StagingClient
    image_repo: str
    sleep: Callable[[float], None]


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


def branch(step: Step) -> Step:
    """병렬 갈래(빌드 ∥ SAST)의 뒤 노드. 다른 갈래가 이미 멈췄으면 기다리지 않고 합류로 넘긴다."""
    @wraps(step)
    def wrapper(state: PipelineState) -> dict[str, Any]:
        return {} if state.get("halt") else step(state)
    return wrapper


def assert_gates_passed(gates: Mapping[str, str], required: tuple[str, ...]) -> None:
    """전이 표가 잘못돼도 판정 없이 다음 단계를 시작하지 못하게 직전에 다시 본다."""
    missing = [gate for gate in required if gates.get(gate) not in PASSING_GATE_RECORDS]
    if missing:
        raise Stopped(f"통과 기록이 없어 진행하지 않는다: {', '.join(missing)}")


class Steps:
    def __init__(self, deps: Deps) -> None:
        self._deps = deps
        self._board = deps.board

    def _read(self, call: Callable[[], T]) -> T:
        """조회는 다시 해도 안전하다. 한 번 실패했다고 멈추면 작업만 계속 돌고 화면은 끝난 것처럼 보인다."""
        for attempt in range(READ_ATTEMPTS - 1):
            try:
                return call()
            except ServiceUnavailable:
                LOG.warning("조회 실패, 다시 시도 (%d/%d)", attempt + 1, READ_ATTEMPTS)
                self._deps.sleep(JENKINS_POLL_SECONDS)
        return call()

    def _ask(self, key: str, node_id: str, what: str, count: int) -> dict[str, Any]:
        self._board.update(node_id, state="waiting", summary=f"{what}: 사람 확인 필요 (항목 {count}건)")
        return {"pending_gate": {"key": key, "node_id": node_id, "what": what, "count": count}}

    def _pass(self, key: str, node_id: str, summary: str) -> dict[str, Any]:
        self._board.update(node_id, state="pass", summary=summary)
        return {"gates": {key: "pass"}, "pending_gate": None}

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

    # ── 입력 ────────────────────────────────────────────
    def prepare(self, _: PipelineState) -> dict[str, Any]:
        options = self._deps.options
        if not REPO_RE.fullmatch(options.repo) or not COMMIT_RE.fullmatch(options.commit):
            self._board.update("prepare", state="fail", summary="허용된 저장소·40자리 커밋 SHA가 아니다")
            raise Stopped("허용되지 않은 저장소이거나 커밋 SHA가 아니다")
        self._board.update("prepare", state="pass", summary=f"{options.repo.removeprefix(REPO_PREFIX)} @ {options.commit[:7]}")
        return {"halt": None}

    # ── Jenkins 공통 ────────────────────────────────────
    def _ensure_build(self, jenkins: JenkinsClient, params: Mapping[str, str], run_key: str) -> int:
        """재개 때 같은 작업을 두 번 시작하지 않는다(RUN_ID 로 큐·이력을 먼저 찾는다)."""
        found = self._read(lambda: jenkins.find_build(run_key))
        return found if found is not None else jenkins.trigger(params, run_key)

    def _finish_job(self, jenkins: JenkinsClient, build: int, deadline_seconds: int, what: str) -> dict[str, Any]:
        deadline = time.time() + deadline_seconds
        while time.time() < deadline:
            status = self._read(lambda: jenkins.status(build))
            if status not in RUNNING_JENKINS_STATES:
                result = self._read(lambda: jenkins.file(build, "result.json")) or {}
                if status != "SUCCESS":
                    raise Stopped(f"{what} 실패 (#{build} {status}) {result.get('error') or ''}".strip())
                return result
            self._deps.sleep(JENKINS_POLL_SECONDS)
        raise ServiceUnavailable(f"{what} #{build} 이 제한 시간 안에 끝나지 않았다. 작업은 계속 돌 수 있다")

    # ── 빌드 ────────────────────────────────────────────
    def build_start(self, _: PipelineState) -> dict[str, Any]:
        options, jenkins = self._deps.options, self._deps.build_jenkins
        self._board.update("build", state="running", summary="빌드 에이전트에서 빌드·푸시 중")
        build = self._ensure_build(jenkins, {"REPO": options.repo, "COMMIT": options.commit,
                                             "IMAGE_REPO": self._deps.image_repo}, f"{self._deps.run_id}-build")
        self._board.add_detail("build", build=build, url=jenkins.console_url(build))
        return {"build": build}

    def build_wait(self, state: PipelineState) -> dict[str, Any]:
        try:
            result = self._finish_job(self._deps.build_jenkins, state["build"], BUILD_DEADLINE_SECONDS, "빌드")
            image = str(result.get("image_ref", ""))
            if result.get("commit") != self._deps.options.commit or "@sha256:" not in image:
                raise Stopped("빌드 결과가 요청한 커밋·digest 와 맞지 않는다")
        except Stopped as stop:
            self._board.update("build", state="fail", summary=str(stop))
            raise
        digest = image.split("@", 1)[1]
        self._board.add_detail("build", image=image)
        self._board.update("build", state="pass", summary=f"이미지 {digest[:19]}")
        return {"image": image, "image_digest": digest}

    # ── SAST ────────────────────────────────────────────
    def sast_start(self, _: PipelineState) -> dict[str, Any]:
        options, run_id = self._deps.options, f"{self._deps.run_id}-sast"
        self._board.update("sast", state="running", summary="소스 점검 중")
        self._deps.sast.start(run_id, options.repo, options.commit)
        self._board.add_detail("sast", run_id=run_id)
        return {"sast_run_id": run_id}

    def sast_wait(self, state: PipelineState) -> dict[str, Any]:
        run_id = state["sast_run_id"]
        deadline = time.time() + SAST_DEADLINE_SECONDS
        while time.time() < deadline:
            scan = self._read(lambda: self._deps.sast.scan(run_id))
            if scan.get("state") != "running":
                return self._sast_gate(scan)
            self._deps.sleep(POLL_SECONDS)
        raise ServiceUnavailable("SAST 점검이 제한 시간 안에 끝나지 않았다")

    def _sast_gate(self, scan: Mapping[str, Any]) -> dict[str, Any]:
        """명세 v1.1 7-1 의 최소판. 점검 실패·커밋 불일치는 멈춤, 비밀값은 사람 확인, 그 밖은 통과."""
        if scan.get("state") != "done":
            self._board.update("sast", state="fail", summary=f"점검 실패: {scan.get('error')}")
            raise Stopped(f"SAST 점검 실패: {scan.get('error')}")
        if scan.get("commit") and scan["commit"] != self._deps.options.commit:
            self._board.update("sast", state="fail", summary="점검한 커밋이 요청과 다르다")
            raise Stopped("SAST 가 점검한 커밋이 요청과 다르다")
        summary = scan.get("summary") or {}
        targets, secrets = int(summary.get("dast_targets") or 0), int((summary.get("counts") or {}).get("secrets") or 0)
        self._board.add_detail("sast", dast_targets=targets, counts=summary.get("counts") or {})
        update = {"dast_targets": targets}
        if secrets:
            return update | self._ask("sast", "sast", "SAST 비밀값", secrets)
        return update | self._pass("sast", "sast", f"DAST 대상 {targets}건 · 비밀값 0")

    # ── 스테이징 ────────────────────────────────────────
    def stage_start(self, state: PipelineState) -> dict[str, Any]:
        assert_gates_passed(state.get("gates") or {}, ("sast",))
        jenkins = self._deps.deploy_jenkins
        self._board.update("stage", state="running", summary="스테이징 데이터 초기화 후 같은 digest 배포 중")
        # HOLDER: 스테이징(과 DB)을 이 실행이 점유한다. 점검이 끝나 풀기 전에는 다른 실행이 배포·초기화하지 못한다.
        build = self._ensure_build(jenkins, {"IMAGE": state["image"], "COMMIT": self._deps.options.commit, "CLOUD": CLOUD,
                                             "HOLDER": self._deps.run_id}, f"{self._deps.run_id}-stage")
        self._board.add_detail("stage", build=build, url=jenkins.console_url(build))
        return {"deploy_build": build}

    def stage_wait(self, state: PipelineState) -> dict[str, Any]:
        try:
            result = self._finish_job(self._deps.deploy_jenkins, state["deploy_build"], STAGE_DEADLINE_SECONDS, "스테이징 배포")
            if result.get("state") != "ready":
                raise Stopped(f"스테이징이 준비되지 않았다: {result.get('state')}")
            if result.get("image_digest") != state["image_digest"]:
                raise Stopped("스테이징에 뜬 이미지 digest 가 빌드 결과와 다르다")
            reset = next((s for s in result.get("steps") or () if s.get("name") == "data_reset"), None)
            remaining = _rows_remaining(reset)
            if remaining != 0:
                raise Stopped(f"스테이징 데이터 초기화 확인 실패(남은 행 {remaining})")
        except Stopped as stop:
            self._board.update("stage", state="fail", summary=str(stop))
            raise
        self._board.add_detail("stage", url=result.get("url"), revision=result.get("revision"),
                               scanner_ip=result.get("scanner_ip"), rows_remaining=0)
        self._board.update("stage", state="pass", summary=f"{result.get('url')} · 초기화 남은 행 0")
        return {"staging_url": str(result.get("url")), "staging_revision": result.get("revision")}

    # ── DAST ────────────────────────────────────────────
    def dast_start(self, state: PipelineState) -> dict[str, Any]:
        if not state.get("dast_targets"):
            # 명세 7-1: 범위 정상 + 후보 0건은 정상적인 0건. 이전 회차 재현 항목(7-3)은 1-A 에서 붙인다.
            self._board.update("dast", state="skipped", summary="SAST 가 넘긴 점검 대상이 없다")
            return {"dast_run_id": None, "gates": {"dast": "pass"}}
        self._board.update("dast", state="running", summary="스테이징을 실제 브라우저로 점검 중")
        report = self._read(lambda: self._deps.sast.sarif(state["sast_run_id"]))
        # 점검 ID 를 실행에서 정해 재개해도 같은 점검을 받는다(입력이 같으면 DAST 가 기존 점검을 돌려준다)
        run_id = str(uuid5(NAMESPACE_URL, f"delta-pipeline/{self._deps.run_id}/dast"))
        binding = {"build_id": f"{self._deps.run_id}-build", "sast_run_id": state["sast_run_id"],
                   "commit_sha": self._deps.options.commit, "image": state["image"],
                   "image_digest": state["image_digest"], "sast_evidence_sha256": evidence_hash(report),
                   "target_url": state["staging_url"], "execution_cloud": CLOUD}
        if state.get("staging_revision"):
            binding["runtime_revision"] = state["staging_revision"]
        self._deps.dast.submit(run_id, binding, report)
        self._board.add_detail("dast", run_id=run_id, target_url=state["staging_url"])
        return {"dast_run_id": run_id}

    def dast_wait(self, state: PipelineState) -> dict[str, Any]:
        run_id = str(state["dast_run_id"])
        deadline = time.time() + DAST_DEADLINE_SECONDS
        while time.time() < deadline:
            view = self._read(lambda: self._deps.dast.view(run_id))
            if view.get("status") not in ("running", "cancelling"):
                return self._dast_gate(view)
            self._deps.sleep(POLL_SECONDS)
        raise ServiceUnavailable("DAST 점검이 제한 시간 안에 끝나지 않았다")

    def _dast_gate(self, view: Mapping[str, Any]) -> dict[str, Any]:
        """명세 v1.1 7-2 의 최소판. 모두 NOT_REPRODUCED 일 때만 자동 통과, 나머지는 사람 확인(D3: 자동 통과 금지)."""
        status = view.get("status")
        results = [{"finding_id": r.get("finding_id"), "status": r.get("status")} for r in view.get("results", ())]
        self._board.add_detail("dast", results=results, evidence_sha256=view.get("evidence_sha256"))
        if status == "skipped":
            return self._pass("dast", "dast", "점검할 후보가 없다(DAST 가 확인)")
        if status != "done":
            # interrupted(점검 서버 재시작)·cancelled·error 는 통과가 아니다
            self._board.update("dast", state="fail", summary=f"점검이 끝나지 않았다: {status}")
            raise Stopped(f"DAST 점검이 끝나지 않았다: {status}")
        missing = len(view.get("finding_ids") or ()) - len(results)
        labels = ", ".join(f"{s} {sum(r['status'] == s for r in results)}" for s in sorted({str(r["status"]) for r in results}))
        if results and missing <= 0 and all(r["status"] in DAST_PASS for r in results):
            return self._pass("dast", "dast", f"재현 없음 ({labels})")
        # 재현·판정 불가·결과 누락은 사람이 본다
        unresolved = sum(r["status"] not in DAST_PASS for r in results) + max(missing, 0)
        return self._ask("dast", "dast", f"DAST ({labels or '결과 없음'}{f', 누락 {missing}' if missing > 0 else ''})",
                         unresolved)

    def _release(self) -> None:
        """스테이징 점유를 푼다. 실패해도 만료(기본 1시간)로 풀리므로 실행 결과를 바꾸지 않는다."""
        try:
            released = self._deps.staging.release(CLOUD, self._deps.run_id)
            self._board.add_detail("stage", lease="released" if released else "none")
        except ServiceUnavailable as exc:
            LOG.warning("스테이징 점유를 풀지 못했다(만료로 풀린다): %s", exc)
            self._board.add_detail("stage", lease="release_failed")

    # ── 끝 ──────────────────────────────────────────────
    def done(self, state: PipelineState) -> dict[str, Any]:
        assert_gates_passed(state.get("gates") or {}, ("sast", "dast"))
        self._release()
        self._board.finish("verified", f"스테이징 검증 완료 · {state['image_digest'][:19]}", passed=True)
        return {"halt": None}

    def halted(self, state: PipelineState) -> dict[str, Any]:
        if state.get("deploy_build") is not None:
            self._release()
        self._board.finish("stopped", str(state.get("halt")))
        return {"halt": state.get("halt")}


def _rows_remaining(step: Mapping[str, Any] | None) -> int | None:
    """스테이징 도구의 data_reset 단계 내용("<Job>: 남은 행 N")에서 N 을 읽는다. 기록이 없으면 확인하지 못한 것이다."""
    found = ROWS_RE.search(str((step or {}).get("detail", "")))
    return int(found.group(1)) if found else None
