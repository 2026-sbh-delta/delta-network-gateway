"""그래프 노드 본문. 노드는 체크포인트를 저장한 뒤 다음으로 넘어가므로, 재개하면 끝나지 않은 노드만 처음부터 다시 돈다.

외부 호출을 시작하는 노드와 끝나기를 기다리는 노드를 나누고, 사람 확인 노드는 interrupt 앞에 부수 효과를 두지 않는다.
게이트 판정은 여기 코드가 한다(명세 v1.1 7장). LLM 은 통과를 정하지 않는다.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Annotated, Any, Protocol, TypedDict, TypeVar
from uuid import NAMESPACE_URL, uuid4, uuid5

from langgraph.types import interrupt

from delta_pipeline.clients import ServiceUnavailable, TransientServiceError, evidence_hash, probe, resolve_cname
from delta_pipeline.delivery import (TARGET_RE, canonical_json, match_runtime, require_postgres18,
                                     sign_approval, validate_bundle, validate_config)
from delta_pipeline.model import REPO_PREFIX, Board, Options

POLL_SECONDS = 1  # 내부 서비스 조회(SAST·DAST·도메인 확인). 끝난 뒤 최대 1초 안에 다음 단계로 간다
CUTOVER_GRACE_SECONDS = 120  # TTL 이 지난 뒤에도 blue 가 확인되지 않으면 이 시간 뒤 되돌린다
CUTOVER_STREAK = 3  # 연속 성공 횟수(에지 캐시·전파 흔들림 배제)
JENKINS_POLL_SECONDS = 1
BUILD_DEADLINE_SECONDS = 1200
STAGE_DEADLINE_SECONDS = 900
# 운영 배포는 VM 생성(과 새 DB 서버면 RDS·Cloud SQL 생성 10~15분)을 포함한다. Jenkinsfile.release 제한과 같다.
RELEASE_DEADLINE_SECONDS = 3600
SAST_DEADLINE_SECONDS = 1200
DAST_DEADLINE_SECONDS = 1800
FIX_DEADLINE_SECONDS = 300  # 수정 에이전트 제한 240초 + 여유
FIX = ("fix", "수정 적용 후 재검증")
FIX_STATUSES = ("REPRODUCED", "BLOCKED")  # 수정안을 만드는 판정(DAST fixer 와 같다)
ROUND_NODES = ("build", "sast", "stage", "dast")
RUNNING_JENKINS_STATES = ("IN_PROGRESS", "NOT_EXECUTED", "PAUSED_PENDING_INPUT", "QUEUED")
PROCEED = ("approve", "확인, 계속 진행")
PASSING_GATE_RECORDS = ("pass", "approved")
DAST_PASS = ("NOT_REPRODUCED",)
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
    diagnosis_pending: bool
    diagnosis_count: int
    diagnosis_request: dict[str, Any] | None
    diagnosis_report: dict[str, Any] | None
    diagnosis_ref: dict[str, Any] | None
    diagnosis_resume: str
    learning_pending: dict[str, Any] | None
    report_failed: bool
    exception_cleanup_done: bool
    failure_type: Annotated[str | None, _first]
    build: int
    image: str
    image_digest: str
    bundle: dict[str, Any] | None
    images: dict[str, str] | None
    image_digests: dict[str, str] | None
    bundle_sha256: str | None
    stage_verified_bundle: str | None
    sast_run_id: str
    dast_targets: int
    deploy_build: int
    staging_url: str
    staging_revision: str | None
    dast_run_id: str | None
    cutover_record_id: str
    cutover_previous: str
    cutover_switched_at: float
    cutover_wait_seconds: int
    # 조치 루프(1-B): 1차 DAST 재현 → 사람이 '수정 적용' → 수정 커밋으로 2차(빌드부터 DAST 재검증까지). 최대 1회.
    round: int
    commit: str  # 이번 회차에 빌드·점검하는 커밋(2차는 수정 커밋). 없으면 입력 커밋
    fix_target: dict[str, str] | None
    first_dast_run_id: str | None
    fix_pr: dict[str, Any] | None
    release_approval: dict[str, Any] | None
    release_config: dict[str, Any] | None
    release_build: int
    release_result: dict[str, Any] | None
    mapping_input_hash: str
    mapping_source: dict[str, Any]
    assisted_origin: dict[str, Any]
    assisted_plan: dict[str, Any]
    assisted_choice: str | None
    manual_count: int
    manual_redeploy: bool
    manual_resume: str
    recovery_pending: bool
    recovery_failure: dict[str, Any]
    recovery_count: int
    recovery_events: list[dict[str, Any]]
    recovery_resume: str
    recovery_previous_run_id: str
    recovery_previous_key: str
    onprem_attempt: int
    failure_code: Annotated[str | None, _first]
    onprem_run_id: str
    onprem_evidence_sha256: str


class Stopped(Exception):
    """사람이 중단했거나 판정·서비스 상태 때문에 멈췄다."""
    def __init__(self, message, *, code=None):
        super().__init__(message)
        self.code = code


class SastClient(Protocol):
    def start(self, run_id: str, repo: str, commit: str) -> dict[str, Any]: ...
    def scan(self, run_id: str) -> dict[str, Any]: ...
    def sarif(self, run_id: str) -> dict[str, Any]: ...


class DastClient(Protocol):
    def submit(self, run_id: str, binding: dict[str, Any], report: dict[str, Any],
               reverify_of: str | None = None) -> dict[str, Any]: ...
    def view(self, run_id: str) -> dict[str, Any]: ...
    def fix(self, run_id: str, finding_id: str) -> dict[str, Any]: ...
    def open_pr(self, run_id: str, finding_id: str, base: str | None = None) -> dict[str, Any]: ...


class StagingClient(Protocol):
    def release(self, cloud: str, holder: str) -> bool: ...


class CloudflareClient(Protocol):
    def find_record(self, zone_id: str, name: str) -> dict[str, Any]: ...
    def set_content(self, zone_id: str, record_id: str, content: str) -> dict[str, Any]: ...


def hostname(value: Any) -> str:
    """CNAME 대상 비교용 정규화(대소문자·마지막 점). IP·URL 은 받지 않는다. 호원님 스크립트(11/5)와 같은 규칙."""
    if not isinstance(value, str):
        raise ValueError("도메인 주소가 아니다")
    name = value.rstrip(".").encode("idna").decode("ascii").lower()
    label = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    if len(name) > 253 or not re.fullmatch(rf"(?:{label}\.)+{label}", name) or name.rsplit(".", 1)[-1].isdigit():
        raise ValueError(f"도메인 주소가 아니다: {value}")
    return name


@dataclass(frozen=True)
class CutoverConfig:
    """서비스 도메인 전환 설정(명세 v1.1 6-3). 오케스트레이터 환경 DELTA_CUTOVER(JSON)로 받는다."""
    zone_id: str
    record: str  # 서비스 도메인(프록시 CNAME)
    green: str  # 지금 가리키는 대상(기존 AWS)
    blue: str  # 옮길 대상(GCP)
    blue_origin: str  # 전환 전 검증에 직접 부를 blue 원본 주소(http(s)://…)
    verify_path: str = "/healthz"
    expect_served_by: str = "gcp"
    scheme: str = "https"  # 서비스 도메인으로 확인할 때 쓰는 방식(원본 인증서가 있으면 https)
    keep_green_seconds: int = 60  # 전환 뒤 green 을 최소한 유지할 시간(호원님 스크립트 기본 60초)
    # 되돌리기(시연 초기화)에서 green 을 확인할 주소·경로. 비우면 https://<green>, verify_path 를 쓴다.
    green_origin: str = ""
    green_verify_path: str = ""

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-fA-F]{32}", self.zone_id):
            raise ValueError("zone_id 는 32자리 16진수다")
        for field in ("record", "green", "blue"):
            object.__setattr__(self, field, hostname(getattr(self, field)))
        if self.green == self.blue or self.record in (self.green, self.blue):
            raise ValueError("record·green·blue 는 서로 다른 도메인이어야 한다")
        if not re.fullmatch(r"https?://[A-Za-z0-9.\-:\[\]]+", self.blue_origin) or not self.verify_path.startswith("/"):
            raise ValueError("blue_origin 은 http(s)://호스트, verify_path 는 / 로 시작해야 한다")
        if (self.green_origin and not re.fullmatch(r"https?://[A-Za-z0-9.\-:\[\]]+", self.green_origin)) or (
                self.green_verify_path and not self.green_verify_path.startswith("/")):
            raise ValueError("green_origin 은 http(s)://호스트, green_verify_path 는 / 로 시작해야 한다")
        if self.scheme not in ("http", "https") or type(self.keep_green_seconds) is not int or self.keep_green_seconds < 60:
            raise ValueError("scheme 은 http/https, keep_green_seconds 는 60 이상 정수여야 한다")


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
    diagnostic: Any = None
    evidence: Any = None
    onprem: Any = None
    inbox: Any = None
    notification_sender: Any = None
    cancelled: Callable[[], bool] = lambda: False
    report_dir: Path | None = None
    ecr_image_repo: str = ""  # 비우지 않으면 빌드가 같은 manifest를 이 ECR 저장소에도 복사한다(AWS 스테이징·운영용)
    cloudflare: CloudflareClient | None = None
    cutover: CutoverConfig | None = None
    probe: Callable[..., tuple[int, str]] = probe
    resolve: Callable[[str], str | None] = resolve_cname
    now: Callable[[], float] = time.time
    release_jenkins: JenkinsClient | None = None
    release_signing_key: bytes | None = field(default=None, repr=False)
    release_config: dict[str, Any] | None = None


def guarded(step: Step) -> Step:
    """멈춤 사유를 상태에 남겨 그래프가 끝맺게 한다. interrupt 는 그대로 통과시킨다."""
    @wraps(step)
    def wrapper(state: PipelineState) -> dict[str, Any]:
        try:
            return step(state)
        except Stopped as stop:
            return {"halt": str(stop), "failure_type": "Stopped", "failure_code": stop.code}
        except ServiceUnavailable as exc:
            return {"halt": f"서비스 응답 없음, 통과로 보지 않는다: {exc}", "failure_type": type(exc).__name__}
    return wrapper


def branch(step: Step) -> Step:
    """병렬 갈래(빌드 ∥ SAST)의 뒤 노드. 다른 갈래가 이미 멈췄으면 기다리지 않고 합류로 넘긴다."""
    @wraps(step)
    def wrapper(state: PipelineState) -> dict[str, Any]:
        return {} if state.get("halt") else step(state)
    return wrapper


GATE_LABEL = {"sast": "SAST", "dast": "DAST", "cutover": "도메인 전환"}


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

    def _commit(self, state: PipelineState) -> str:
        return state.get("commit") or self._deps.options.commit

    def _key(self, state: PipelineState, name: str) -> str:
        """Jenkins·SAST 작업 키. 2차는 다른 키를 써서 1차 작업을 다시 찾지 않는다."""
        round_ = state.get("round") or 1
        key = f"{self._deps.run_id}-{name}" if round_ == 1 else f"{self._deps.run_id}-r{round_}-{name}"
        key += f"-recovery{state['onprem_attempt']}" if name == "onprem" and state.get("onprem_attempt") else ""
        # Bound external IDs without losing step, round or recovery identity.
        return hashlib.sha256(key.encode()).hexdigest() if len(key) > 64 else key

    def _ask(self, key: str, node_id: str, what: str, count: int, **extra: Any) -> dict[str, Any]:
        self._board.update(node_id, state="waiting", summary=f"{what}: 사람 확인 필요 (항목 {count}건)")
        return {"pending_gate": {"key": key, "node_id": node_id, "what": what, "count": count, **extra}}

    def _pass(self, key: str, node_id: str, summary: str) -> dict[str, Any]:
        self._board.update(node_id, state="pass", summary=summary)
        return {"gates": {key: "pass"}, "pending_gate": None}

    def ask_gate(self, state: PipelineState) -> dict[str, Any]:
        gate = state["pending_gate"] or {}
        what = gate["what"]
        fixable = gate.get("fix_target") is not None
        options = [FIX, PROCEED, ("stop", "중단")] if fixable else [PROCEED, ("stop", "중단")]
        answer = interrupt({**({"case_id": state["diagnosis_ref"]["case_id"]} if self._deps.options.assistance and state.get("diagnosis_ref") else {}), "node_id": gate["node_id"], "prompt": f"{what} 결과를 보고 다음 단계로 갈지 정합니다.",
                            "options": options})
        if state.get("diagnosis_ref"):
            from delta_pipeline.exception_flow import record_review
            record_review(self._deps, state, answer)
        if answer == "fix" and fixable:
            self._board.update(gate["node_id"], state="fail", summary=f"{what}: 재현 확인 → 수정 적용 선택")
            return {"fix_target": gate["fix_target"], "pending_gate": None}
        if answer != "approve":
            self._board.update(gate["node_id"], state="fail", summary=f"{what}: 사람이 중단")
            raise Stopped(f"{what}에서 사람이 중단했다")
        if gate.get("zero_candidate_scope"):
            self._board.add_detail("dast", scope_acknowledged=True)
        self._board.update(gate["node_id"], state="pass", summary=f"{what}: 사람 승인 후 진행 (항목 {gate['count']}건)")
        return {"gates": {gate["key"]: "approved"}, "pending_gate": None}

    # ── 입력 ────────────────────────────────────────────
    def prepare(self, _: PipelineState) -> dict[str, Any]:
        options = self._deps.options
        if not REPO_RE.fullmatch(options.repo) or not COMMIT_RE.fullmatch(options.commit):
            self._board.update("prepare", state="fail", summary="허용된 저장소·40자리 커밋 SHA가 아니다")
            raise Stopped("허용되지 않은 저장소이거나 커밋 SHA가 아니다")
        if options.staging_cloud not in ("aws", "gcp"):
            self._board.update("prepare", state="fail", summary="스테이징 cloud는 aws 또는 gcp여야 한다")
            raise Stopped("지원하지 않는 스테이징 cloud다")
        if options.release_target is not None and (not isinstance(options.release_target, str)
                                                   or not TARGET_RE.fullmatch(options.release_target)):
            self._board.update("prepare", state="fail", summary="단일 release target_id가 아니다")
            raise Stopped("운영 대상은 하나의 target_id여야 한다")
        if options.cutover and options.release_target is None:
            self._board.update("prepare", state="fail", summary="운영 release target 없이 도메인을 전환할 수 없다")
            raise Stopped("운영 release target·배포 verify 없이 도메인 전환을 허용하지 않는다")
        self._board.update("prepare", state="pass", summary=f"{options.repo.removeprefix(REPO_PREFIX)} @ {options.commit[:7]}")
        config = None
        if options.release_target is not None and self._deps.release_config is not None:
            try:
                config = validate_config(options.release_target, self._deps.release_config)
            except (ValueError, TypeError) as exc:
                self._board.update("prepare", state="fail", summary="운영 인벤토리를 확인할 수 없다")
                raise Stopped("운영 인벤토리를 확인할 수 없다") from exc
        return {"halt": None, "release_config": config}

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
    def build_start(self, state: PipelineState) -> dict[str, Any]:
        options, jenkins = self._deps.options, self._deps.build_jenkins
        self._board.update("build", state="running", summary="빌드 에이전트에서 빌드·푸시 중")
        params = {"REPO": options.repo, "COMMIT": self._commit(state), "IMAGE_REPO": self._deps.image_repo}
        # ECR 사본은 AWS 가 쓸 때만 만든다(AWS 스테이징 또는 AWS 운영 대상). GCP 만 쓰면 복사 시간을 쓰지 않는다
        uses_aws = options.staging_cloud == "aws" or (self._deps.release_config or {}).get("cloud") == "aws"
        if self._deps.ecr_image_repo and uses_aws:
            params["ECR_IMAGE_REPO"] = self._deps.ecr_image_repo
        build = self._ensure_build(jenkins, params, self._key(state, "build"))
        self._board.add_detail("build", build=build, url=jenkins.console_url(build))
        return {"build": build}

    def build_wait(self, state: PipelineState) -> dict[str, Any]:
        try:
            result = self._finish_job(self._deps.build_jenkins, state["build"], BUILD_DEADLINE_SECONDS, "빌드")
            image = str(result.get("image_ref", ""))
            if result.get("commit") != self._commit(state) or "@sha256:" not in image:
                raise Stopped("빌드 결과가 요청한 커밋·digest 와 맞지 않는다")
            bundle = None
            if any(key in result for key in ("schema", "images", "image_digests", "bundle_sha256", "platform")):
                try:
                    bundle = validate_bundle(result, self._commit(state))
                except ValueError as exc:
                    raise Stopped(str(exc)) from exc
        except Stopped as stop:
            self._board.update("build", state="fail", summary=str(stop))
            raise
        digest = image.split("@", 1)[1]
        self._board.add_detail("build", image=image, **({"bundle": bundle} if bundle else {}))
        self._board.update("build", state="pass", summary=f"이미지 {digest[:19]}")
        return {"image": image, "image_digest": digest, "bundle": bundle,
                "images": bundle["images"] if bundle else None,
                "image_digests": bundle["image_digests"] if bundle else None,
                "bundle_sha256": bundle["bundle_sha256"] if bundle else None,
                "stage_verified_bundle": None}

    # ── SAST ────────────────────────────────────────────
    def sast_start(self, state: PipelineState) -> dict[str, Any]:
        options, run_id = self._deps.options, self._key(state, "sast")
        self._board.update("sast", state="running", summary="소스 점검 중")
        try:
            started = self._deps.sast.start(run_id, options.repo, self._commit(state))
        except ServiceUnavailable as exc:
            self._board.update("sast", state="fail", summary=str(exc))
            raise
        if options.execution_cloud == "onprem":
            from uuid import UUID
            run_id = str(UUID(started["run_id"]))
        self._board.add_detail("sast", run_id=run_id)
        return {"sast_run_id": run_id}

    def sast_wait(self, state: PipelineState) -> dict[str, Any]:
        run_id = state["sast_run_id"]
        deadline = time.time() + SAST_DEADLINE_SECONDS
        while time.time() < deadline:
            scan = self._read(lambda: self._deps.sast.scan(run_id))
            if scan.get("state") != "running":
                return self._sast_gate(scan, self._commit(state))
            self._deps.sleep(POLL_SECONDS)
        raise ServiceUnavailable("SAST 점검이 제한 시간 안에 끝나지 않았다")

    def _sast_gate(self, scan: Mapping[str, Any], commit: str) -> dict[str, Any]:
        """명세 v1.1 7-1 의 최소판.

        점검 실패·요청 커밋 불일치는 멈춤. 결과 요약이나 점검 범위 증거(실제 커밋·파일 수)가 없거나 다르면
        정상적인 0건으로 보지 않고 사람 확인. 비밀값도 사람 확인. 그 밖은 통과.
        """
        if scan.get("state") != "done":
            self._board.update("sast", state="fail", summary=f"점검 실패: {scan.get('error')}")
            raise Stopped(f"SAST 점검 실패: {scan.get('error')}")
        if scan.get("commit") and scan["commit"] != commit:
            self._board.update("sast", state="fail", summary="점검한 커밋이 요청과 다르다")
            raise Stopped("SAST 가 점검한 커밋이 요청과 다르다")
        summary = scan.get("summary")
        summary = summary if isinstance(summary, Mapping) else {}
        scope = summary.get("scope")
        scope = scope if isinstance(scope, Mapping) else {}
        counts = summary.get("counts")
        counts = counts if isinstance(counts, Mapping) else {}
        self._board.add_detail("sast", scope=scope, counts=counts)
        problem = ("결과 요약 없음" if not summary else "점검 범위 증거 없음" if not scope
                   else "실제 점검 커밋 다름" if scope.get("commit") != commit
                   else "점검한 소스 파일 0개" if not scope.get("files")
                   else "점검한 소스 파일 수 불명" if type(scope.get("files")) is not int or scope["files"] < 0 else None)
        if problem:
            # 0건이 정상인지 확인할 수 없다. 자동 통과하지 않는다.
            return {"dast_targets": 0} | self._ask("sast", "sast", f"SAST 범위 확인({problem})", 1)
        targets, secrets = summary.get("dast_targets"), counts.get("secrets")
        if type(targets) is not int or targets < 0 or type(secrets) is not int or secrets < 0:
            return {"dast_targets": 0} | self._ask("sast", "sast", "SAST 결과 수 불명", 1)
        self._board.add_detail("sast", dast_targets=targets)
        update = {"dast_targets": targets}
        if secrets:
            return update | self._ask("sast", "sast", "SAST 비밀값", secrets)
        return update | self._pass("sast", "sast", f"DAST 대상 {targets}건 · 비밀값 0 · 파일 {scope['files']}")

    # ── 스테이징 ────────────────────────────────────────
    def stage_start(self, state: PipelineState) -> dict[str, Any]:
        assert_gates_passed(state.get("gates") or {}, ("sast",))
        jenkins = self._deps.deploy_jenkins
        self._board.update("stage", state="running", summary="스테이징 데이터 초기화 후 같은 digest 배포 중")
        # HOLDER: 스테이징(과 DB)을 이 실행이 점유한다. 점검이 끝나 풀기 전에는 다른 실행이 배포·초기화하지 못한다.
        params = {"IMAGE": state["image"], "COMMIT": self._commit(state), "CLOUD": self._deps.options.staging_cloud,
                  "HOLDER": self._deps.run_id}
        if state.get("bundle"):
            params |= {"IMAGES_JSON": canonical_json(state["images"]), "BUNDLE_SHA256": state["bundle_sha256"]}
        build = self._ensure_build(jenkins, params, self._key(state, "stage"))
        self._board.add_detail("stage", build=build, url=jenkins.console_url(build))
        return {"deploy_build": build}

    def stage_wait(self, state: PipelineState) -> dict[str, Any]:
        try:
            result = self._finish_job(self._deps.deploy_jenkins, state["deploy_build"], STAGE_DEADLINE_SECONDS, "스테이징 배포")
            if result.get("state") != "ready":
                raise Stopped(f"스테이징이 준비되지 않았다: {result.get('state')}")
            if result.get("image_digest") != state["image_digest"]:
                raise Stopped("스테이징에 뜬 이미지 digest 가 빌드 결과와 다르다")
            if state.get("bundle"):
                try:
                    match_runtime(result, state["bundle"])
                    require_postgres18(result)
                    remaining = result.get("data_reset_remaining")
                    if type(remaining) is not int or remaining != 0:
                        raise ValueError("스테이징 구조화 초기화 결과가 정수 0이 아니다")
                except ValueError as exc:
                    raise Stopped(str(exc)) from exc
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
        return {"staging_url": str(result.get("url")), "staging_revision": result.get("revision"),
                "stage_verified_bundle": state.get("bundle_sha256")}

    # ── DAST ────────────────────────────────────────────
    def _ask_empty_dast(self, state: PipelineState) -> dict[str, Any]:
        self._board.add_detail("dast", dast_check="not_run", results=[],
            coverage="sast_candidates_only", reason="no_supported_candidates",
            scope_acknowledged=False, deployment_approved=False,
            zero_candidate_scope={"commit": self._commit(state),
                "sast_run_id": state.get("sast_run_id"),
                "bundle_sha256": state.get("bundle_sha256"),
                "runtime_revision": state.get("staging_revision")})
        return self._ask("dast", "dast", "DAST 미실행·대상 환경 검사 범위 확인", 0,
                         zero_candidate_scope=True)

    def dast_start(self, state: PipelineState) -> dict[str, Any]:
        previous = state.get("first_dast_run_id")
        if not state.get("dast_targets") and not previous:
            # No scanner execution is a scope exception, not an automatic security pass.
            # Reverification still checks earlier findings even if SAST now has none.
            return {"dast_run_id": None} | self._ask_empty_dast(state)
        self._board.update("dast", state="running", summary="수정 후 재검증: 1차 재현 항목을 다시 점검 중" if previous
                           else "스테이징을 실제 브라우저로 점검 중")
        report = self._read(lambda: self._deps.sast.sarif(state["sast_run_id"]))
        findings = [item for run in report.get("runs", []) for item in run.get("results", [])]
        self._board.add_detail("sast", diagnostic_findings=[{
            "rule": item.get("ruleId"), "message": str(item.get("message", {}).get("text", ""))[:500],
            "locations": [loc.get("physicalLocation", {}) for loc in item.get("locations", [])[:2]],
        } for item in findings[:2]])
        # 점검 ID 를 실행에서 정해 재개해도 같은 점검을 받는다(입력이 같으면 DAST 가 기존 점검을 돌려준다)
        round_ = state.get("round") or 1
        run_id = str(uuid5(NAMESPACE_URL, f"delta-pipeline/{self._deps.run_id}/dast"
                           + ("" if round_ == 1 else f"/r{round_}")))
        binding = {"build_id": self._key(state, "build"), "sast_run_id": state["sast_run_id"],
                   "commit_sha": self._commit(state), "image": state["image"],
                   "image_digest": state["image_digest"], "sast_evidence_sha256": evidence_hash(report),
                   "target_url": state["staging_url"], "execution_cloud": self._deps.options.staging_cloud}
        if state.get("bundle"):
            binding["images"] = dict(state["images"])
        if state.get("staging_revision"):
            binding["runtime_revision"] = state["staging_revision"]
        self._deps.dast.submit(run_id, binding, report, previous)
        self._board.add_detail("dast", run_id=run_id, target_url=state["staging_url"])
        return {"dast_run_id": run_id}

    def dast_wait(self, state: PipelineState) -> dict[str, Any]:
        run_id = str(state["dast_run_id"])
        deadline = time.time() + DAST_DEADLINE_SECONDS
        while time.time() < deadline:
            view = self._read(lambda: self._deps.dast.view(run_id))
            if view.get("status") not in ("running", "cancelling"):
                return self._dast_gate(view, state)
            self._deps.sleep(POLL_SECONDS)
        raise ServiceUnavailable("DAST 점검이 제한 시간 안에 끝나지 않았다")

    def _dast_gate(self, view: Mapping[str, Any], state: PipelineState) -> dict[str, Any]:
        """명세 v1.1 7-2 의 최소판. 모두 NOT_REPRODUCED 일 때만 자동 통과, 나머지는 사람 확인(D3: 자동 통과 금지)."""
        binding = view.get("binding") or {}
        self._board.add_detail("dast", diagnostic_evidence={
            "mode": "live_orchestrator", "source_pipeline_run_id": self._deps.run_id,
            "sast_run_id": state.get("sast_run_id"), "dast_run_id": state.get("dast_run_id"),
            "commit": self._commit(state), "images": state.get("images"),
            "bundle_sha256": state.get("bundle_sha256"), "runtime_revision": state.get("staging_revision"),
            "sarif_sha256": binding.get("sast_evidence_sha256"),
            "coverage": view.get("coverage", "sast_candidates_only"), "deployment_approved": False,
        })
        status = view.get("status")
        results = [{"finding_id": r.get("finding_id"), "status": r.get("status")} for r in view.get("results", ())]
        self._board.add_detail("dast", results=results, evidence_sha256=view.get("evidence_sha256"))
        if status == "skipped":
            if (state.get("dast_targets") or state.get("first_dast_run_id") or results
                    or view.get("finding_ids") or view.get("eligible_count")):
                return self._ask("dast", "dast", "DAST 후보가 있지만 실행 근거 없음", 1)
            return self._ask_empty_dast(state)
        if status != "done":
            # interrupted(점검 서버 재시작)·cancelled·error 는 통과가 아니다
            self._board.update("dast", state="fail", summary=f"점검이 끝나지 않았다: {status}")
            raise Stopped(f"DAST 점검이 끝나지 않았다: {status}")
        # 요청한 후보 ID 와 결과 ID 를 대조한다. 누락·중복·예상 밖 항목, 후보가 10개를 넘어 잘린 경우는 통과가 아니다.
        expected = list(view.get("finding_ids") or ())
        got = [r["finding_id"] for r in results]
        missing = len(set(expected) - set(got))
        extra = len(set(got) - set(expected)) + (len(got) - len(set(got)))
        truncated = int(view.get("eligible_count") or len(expected)) - len(expected)
        labels = ", ".join(f"{s} {sum(r['status'] == s for r in results)}" for s in sorted({str(r["status"]) for r in results}))
        notes = [n for n in (f"누락 {missing}" if missing else "", f"예상 밖 {extra}" if extra else "",
                             f"미점검 {truncated}(점검 상한 초과)" if truncated > 0 else "") if n]
        prefix = "수정 후 재검증 · " if (state.get("round") or 1) > 1 else ""
        if results and not notes and all(r["status"] in DAST_PASS for r in results):
            return self._pass("dast", "dast", f"{prefix}재현 없음 ({labels})")
        # 재현·판정 불가·결과 누락·미점검은 사람이 본다. 1차에서 재현된 항목이 있으면 '수정 적용'을 고를 수 있다(1회)
        unresolved = sum(r["status"] not in DAST_PASS for r in results) + missing + extra + max(truncated, 0)
        hit = next((r["finding_id"] for r in results if r["status"] in FIX_STATUSES), None)
        fix_target = ({"dast_run_id": str(state["dast_run_id"]), "finding_id": str(hit)}
                      if hit and (state.get("round") or 1) == 1 else None)
        return self._ask("dast", "dast", f"{prefix}DAST ({', '.join([labels or '결과 없음', *notes])})", unresolved,
                         fix_target=fix_target)

    # ── 조치 루프(1-B) ──────────────────────────────────
    def fix_apply(self, state: PipelineState) -> dict[str, Any]:
        """사람이 고른 '수정 적용': DAST 가 재현 직후 만든 수정안을 PR 로 올리고, 그 커밋으로 2차를 시작한다.

        수정안은 DAST 수정 에이전트(SAST·DAST 근거, 작업 사본만 수정)가 만든다. 커밋 SHA 는 원격에 올라간 값만 받는다.
        """
        target = state.get("fix_target") or {}
        run_id, finding_id = target.get("dast_run_id", ""), target.get("finding_id", "")
        self._board.update("fix", state="running", summary="수정안 생성 중(SAST·DAST 근거)")
        self._board.add_detail("fix", dast_run_id=run_id, finding_id=finding_id)
        try:
            fix = self._await_fix(run_id, finding_id)
            self._board.add_detail("fix", summary=fix.get("summary"), files=fix.get("files"),
                                   changes=fix.get("changes"), trace=fix.get("trace"), patch=fix.get("patch"))
            self._board.update("fix", summary="수정안 준비 · PR 올리는 중")
            pr = fix.get("pr") or self._deps.dast.open_pr(run_id, finding_id, self._deps.options.branch or None)
            sha = str(pr.get("commit_sha") or "")
            if not COMMIT_RE.fullmatch(sha):
                raise Stopped(f"수정 커밋을 원격에서 확인하지 못했다({pr.get('status') or '응답 없음'})")
        except Stopped as stop:
            self._board.update("fix", state="fail", summary=str(stop))
            raise
        except ServiceUnavailable as exc:
            self._board.update("fix", state="fail", summary=f"수정안·PR 요청 실패: {exc}")
            raise
        pr_brief = {"url": pr.get("url"), "number": pr.get("number"), "branch": pr.get("branch"), "commit": sha}
        self._board.add_detail("fix", pr=pr_brief)
        label = f"PR #{pr.get('number')}" if pr.get("number") else str(pr.get("branch"))
        self._board.update("fix", state="pass", summary=f"{label} · 커밋 {sha[:7]} · 파일 {len(fix.get('files') or [])}개")
        self._board.new_round(ROUND_NODES, 2)
        # 2차: 새 커밋으로 빌드부터 다시. 게이트 기록을 비워 2차 판정 없이 넘어가지 못하게 한다
        return {"round": 2, "commit": sha, "fix_target": None, "fix_pr": pr_brief,
                "first_dast_run_id": run_id, "gates": {"sast": "", "dast": "", "release": "", "release_approval": ""},
                "dast_targets": 0, "bundle": None, "images": None, "image_digests": None, "bundle_sha256": None,
                "stage_verified_bundle": None, "release_approval": None, "release_result": None}

    def _await_fix(self, run_id: str, finding_id: str) -> dict[str, Any]:
        deadline = time.time() + FIX_DEADLINE_SECONDS
        while time.time() < deadline:
            fix = self._read(lambda: self._deps.dast.fix(run_id, finding_id))
            if fix.get("status") == "ready" and fix.get("patch"):
                return fix
            if fix.get("status") in ("failed", "none"):
                raise Stopped(f"수정안을 만들지 못했다: {fix.get('error') or fix.get('status')}")
            self._deps.sleep(POLL_SECONDS)
        raise Stopped("수정안이 제한 시간 안에 만들어지지 않았다")

    def _release(self) -> None:
        if self._deps.options.execution_cloud == 'onprem':
            return
        """스테이징 점유를 푼다. 실패해도 만료(기본 1시간)로 풀리므로 실행 결과를 바꾸지 않는다."""
        try:
            released = self._deps.staging.release(self._deps.options.staging_cloud, self._deps.run_id)
            self._board.add_detail("stage", lease="released" if released else "none")
        except ServiceUnavailable as exc:
            LOG.warning("스테이징 점유를 풀지 못했다(만료로 풀린다): %s", exc)
            self._board.add_detail("stage", lease="release_failed")

    # ── 운영 배포: 승인 → 서명 → Jenkins → 실행 근거 검증 ──
    def _production_bundle(self, state: PipelineState) -> dict[str, Any]:
        if self._deps.options.release_target is None:
            raise Stopped("운영 release target 설정이 없다")
        if any((state.get("gates") or {}).get(gate) != "pass" for gate in ("sast", "dast")):
            raise Stopped("SAST·DAST 미해결/알 수 없는 판정은 운영을 승인할 수 없다")
        try:
            bundle = validate_bundle(state.get("bundle") or {}, self._commit(state))
        except ValueError as exc:
            raise Stopped(str(exc)) from exc
        if (state.get("stage_verified_bundle") != bundle["bundle_sha256"]
                or state.get("images") != bundle["images"] or state.get("image_digests") != bundle["image_digests"]
                or state.get("bundle_sha256") != bundle["bundle_sha256"]
                or state.get("image") != bundle["image_ref"] or state.get("image_digest") != bundle["image_digest"]):
            raise Stopped("현재 묶음의 스테이징 검증 기록이 없다")
        return bundle

    @staticmethod
    def _target_bundle(bundle: dict[str, Any], cloud: str) -> dict[str, Any]:
        """운영 대상 클라우드의 사본(AWS=ECR, GCP=AR). digest·묶음 해시는 같고 레지스트리 주소만 다르다."""
        registry = "aws" if cloud == "onprem" else cloud
        images = (bundle.get("registry_images") or {}).get(registry) or bundle["images"]
        return {**bundle, "images": dict(images), "image_ref": images["frontend"]}

    def _release_config(self, state: PipelineState) -> dict[str, Any]:
        try:
            target = self._deps.options.release_target
            config = validate_config(target, state.get("release_config"))
            current = validate_config(target, self._deps.release_config)
            if canonical_json(config) != canonical_json(current):
                raise ValueError("운영 설정이 승인 대상과 다르다")
        except (TypeError, ValueError) as exc:
            raise Stopped("운영 인벤토리 누락·변경으로 진행할 수 없다") from exc
        return config

    def ask_release(self, state: PipelineState) -> dict[str, Any]:
        bundle = self._production_bundle(state)
        config = self._release_config(state)
        if self._deps.release_jenkins is None or self._deps.release_signing_key is None:
            raise Stopped("운영 Jenkins 또는 Ed25519 서명 키가 없다")
        target = self._deps.options.release_target
        config_hash = evidence_hash(config)
        answer = interrupt({"node_id": "release", "prompt": f"운영 배포 승인: {target} · {bundle['commit']} · "
                            f"bundle {bundle['bundle_sha256']} · config {config_hash}",
                            "options": [("approve", "운영 배포 승인"), ("stop", "중단")]})
        if answer != "approve":
            self._board.update("release", state="fail", summary="운영 배포를 사람이 중단")
            raise Stopped("운영 배포를 사람이 중단했다")
        claims = {"deployment_id": self._key(state, "release"), "commit_sha": bundle["commit"],
                  "bundle_sha256": bundle["bundle_sha256"], "target_id": target, "environment": "production",
                  "config_sha256": config_hash, "exp": int(self._deps.now()) + 600, "nonce": uuid4().hex}
        try:
            approval = sign_approval(claims, self._deps.release_signing_key)
        except ValueError as exc:
            self._board.update("release", state="fail", summary="운영 승인 서명 실패")
            raise Stopped("운영 승인 서명 실패") from exc
        self._board.update("release", state="running", summary="사람 승인 완료 · 운영 Jenkins 접수 준비")
        # 원문 토큰과 키는 Board·이벤트 로그에 넣지 않는다. 토큰만 체크포인트에 보존한다.
        return {"release_approval": approval, "gates": {"release_approval": "approved"}}

    def release_start(self, state: PipelineState) -> dict[str, Any]:
        bundle = self._production_bundle(state)
        config = self._release_config(state)
        approval = state.get("release_approval") or {}
        claims = approval.get("claims") or {}
        expected = {"deployment_id": self._key(state, "release"), "commit_sha": bundle["commit"],
                    "bundle_sha256": bundle["bundle_sha256"], "target_id": config["target_id"],
                    "environment": "production", "config_sha256": evidence_hash(config)}
        if ((state.get("gates") or {}).get("release_approval") != "approved"
                or any(claims.get(k) != v for k, v in expected.items())
                or not approval.get("signature") or not claims.get("nonce")):
            raise Stopped("현재 묶음·대상·설정에 대한 사람 승인 토큰이 없다")
        jenkins = self._deps.release_jenkins
        if jenkins is None:
            raise Stopped("운영 Jenkins가 없다")
        # 응답 유실·재시작 때 기존 작업을 먼저 찾는다. 새 토큰이나 deployment_id를 만들지 않는다.
        build = self._read(lambda: jenkins.find_build(expected["deployment_id"]))
        if build is None:
            expiry = claims.get("exp")
            if type(expiry) is not int or not self._deps.now() < expiry <= self._deps.now() + 600:
                raise Stopped("운영 승인 토큰이 만료됐거나 유효 기간이 잘못됐다")
            target_images = self._target_bundle(bundle, config["cloud"])["images"]
            params = {"TARGET_ID": config["target_id"], "COMMIT": bundle["commit"],
                      "BUNDLE_SHA256": bundle["bundle_sha256"], "IMAGES_JSON": canonical_json(target_images),
                      "APPROVAL": canonical_json(approval),
                      # 클라우드별 사본(registry_images)까지 담은 빌드 결과. AWS 대상은 ECR 사본으로 배포한다
                      "BUNDLE_JSON": canonical_json({**bundle, "schema": 2, "platform": "linux/amd64"})}
            build = jenkins.trigger(params, expected["deployment_id"])
        self._board.add_detail("release", build=build, url=jenkins.console_url(build),
                               deployment_id=expected["deployment_id"], target_id=config["target_id"],
                               bundle_sha256=bundle["bundle_sha256"], config_sha256=expected["config_sha256"])
        return {"release_build": build}

    def release_wait(self, state: PipelineState) -> dict[str, Any]:
        self._production_bundle(state)
        if self._deps.release_jenkins is None:
            raise Stopped("운영 Jenkins가 없다")
        try:
            result = self._finish_job(self._deps.release_jenkins, state["release_build"], RELEASE_DEADLINE_SECONDS, "운영 배포")
        except Stopped as exc:
            self._board.update("release", state="fail", summary=str(exc))
            raise
        # 토큰·비밀 등 제어기 응답의 예상 밖 필드는 체크포인트·화면에 복사하지 않는다.
        keys = ("state", "target_id", "environment", "commit", "images", "image_digests", "bundle_sha256",
                "image_digest", "schema", "platform", "image_ref", "database_version_num", "deployment_id", "config_sha256",
                "environment_id", "resource_binding", "service_url", "telemetry_service_id")
        return {"release_result": {key: result[key] for key in keys if key in result}}

    def _verified_release(self, state: PipelineState) -> None:
        bundle = self._production_bundle(state)
        result = state.get("release_result") or {}
        if (result.get("state") != "verified" or result.get("target_id") != self._deps.options.release_target
                or result.get("environment") != "production"):
            raise Stopped("운영 배포의 대상·verified 실행 기록이 없다")
        try:
            # 운영 결과는 대상 클라우드 사본으로 실행된다(AWS=ECR). digest·묶음 해시는 같다.
            match_runtime(result, self._target_bundle(bundle, self._release_config(state)["cloud"]))
            require_postgres18(result)
        except ValueError as exc:
            raise Stopped(str(exc)) from exc
        config = self._release_config(state)
        if ("deployment_id" in result and result["deployment_id"] != self._key(state, "release")
                or "config_sha256" in result and result["config_sha256"] != evidence_hash(config)):
            raise Stopped("운영 결과의 배포 ID·설정 해시가 승인과 다르다")

    def release_verify(self, state: PipelineState) -> dict[str, Any]:
        try:
            self._verified_release(state)
        except Stopped as exc:
            self._board.update("release", state="fail", summary=str(exc))
            raise
        self._board.update("release", state="pass", summary=f"운영 검증 완료 · {self._deps.options.release_target}")
        self._board.add_detail("release", verified_result=state["release_result"])
        return {"gates": {"release": "pass"}}

    def _require_release(self, state: PipelineState) -> None:
        if (state.get("gates") or {}).get("release") != "pass":
            raise Stopped("운영 배포 verify 통과 기록이 없어 도메인 전환·운영 완료를 허용하지 않는다")
        self._verified_release(state)

    # ── 서비스 도메인 전환(명세 v1.1 6-3) ─────────────────
    def _cutover(self) -> tuple[CutoverConfig, CloudflareClient]:
        if self._deps.cutover is None or self._deps.cloudflare is None:
            raise Stopped("서비스 도메인 전환 설정(DELTA_CUTOVER·Cloudflare 토큰)이 없다")
        return self._deps.cutover, self._deps.cloudflare

    def ask_cutover(self, state: PipelineState) -> dict[str, Any]:
        self._require_release(state)
        cfg = self._deps.cutover
        target = f"{cfg.record}: {cfg.green} → {cfg.blue}" if cfg else "설정 없음"
        answer = interrupt({"node_id": "cutover", "prompt": f"서비스 도메인을 옮깁니다({target}). 기존 대상은 내리지 않습니다.",
                            "options": [PROCEED, ("stop", "중단")]})
        if answer != "approve":
            self._board.update("cutover", state="fail", summary="사람이 중단")
            raise Stopped("서비스 도메인 전환을 사람이 중단했다")
        self._board.update("cutover", state="running", summary="전환 전 검증 중")
        return {"gates": {"cutover_approval": "approved"}}

    def cutover_check(self, state: PipelineState) -> dict[str, Any]:
        """DNS 를 바꾸기 전에 blue 원본이 서비스 도메인 요청에 정상 응답하는지 본다. 실패하면 DNS 를 건드리지 않는다."""
        assert_gates_passed(state.get("gates") or {}, ("sast", "dast", "cutover_approval"))
        self._require_release(state)
        cfg, _ = self._cutover()
        status, served = self._deps.probe(cfg.blue_origin + cfg.verify_path, host=cfg.record)
        self._board.add_detail("cutover", precheck={"status": status, "served_by": served})
        if status != 200 or not served.startswith(cfg.expect_served_by):
            self._board.update("cutover", state="fail", summary=f"전환 전 검증 실패(HTTP {status}, {served or '식별값 없음'})")
            raise Stopped(f"blue 원본 검증 실패(HTTP {status}, X-Served-By={served!r}). DNS 를 바꾸지 않았다")
        return {}

    def cutover_switch(self, state: PipelineState) -> dict[str, Any]:
        """DNS 전용 CNAME 의 대상을 green 에서 blue 로 바꾼다(호원님 스크립트 11/5 와 같은 규칙).

        레코드는 미리 만든 DNS 전용 CNAME 이어야 한다. 현재 값이 green 일 때만 바꾸고, 바꾼 뒤 다시 읽어 확인한다.
        green 은 기존 TTL(자동이면 300초)과 최소 유지 시간 중 긴 쪽만큼 그대로 둔다.
        """
        self._require_release(state)
        cfg, cf = self._cutover()
        record = self._read(lambda: cf.find_record(cfg.zone_id, cfg.record))
        if record.get("type") != "CNAME" or record.get("proxied") is not False:
            raise Stopped(f"{cfg.record} 는 DNS 전용 CNAME 이어야 한다(type={record.get('type')}, proxied={record.get('proxied')})")
        ttl = record.get("ttl")
        if type(ttl) is not int or not (ttl == 1 or 30 <= ttl <= 86400):
            raise Stopped(f"{cfg.record} 의 TTL 을 확인할 수 없다({ttl!r})")
        wait_seconds = max(300 if ttl == 1 else ttl, cfg.keep_green_seconds)
        current = hostname(record.get("content"))
        if current == cfg.blue:
            # 재개: 앞 실행에서 이미 바꿨다. 되돌릴 값은 green, 전환 시각은 처음 기록을 쓴다.
            self._board.add_detail("cutover", switch="already_blue")
            return {"cutover_record_id": record["id"], "cutover_previous": state.get("cutover_previous") or cfg.green,
                    "cutover_switched_at": state.get("cutover_switched_at") or self._deps.now(),
                    "cutover_wait_seconds": state.get("cutover_wait_seconds") or wait_seconds}
        if current != cfg.green:
            raise Stopped(f"현재 대상({current})이 green({cfg.green})이 아니다. 사람이 확인해야 한다")
        self._board.add_detail("cutover", previous=current, ttl=ttl, wait_seconds=wait_seconds)
        cf.set_content(cfg.zone_id, record["id"], cfg.blue)
        switched_at = self._deps.now()
        changed = self._read(lambda: cf.find_record(cfg.zone_id, cfg.record))
        if (hostname(changed.get("content")) != cfg.blue or changed.get("proxied") is not False
                or changed.get("ttl") != ttl):
            raise Stopped("변경 뒤 레코드가 blue·DNS 전용·기존 TTL 이 아니다")
        self._board.update("cutover", summary=f"{cfg.record} → {cfg.blue}(설정 반영), 실제 DNS·응답 확인 중")
        return {"cutover_record_id": record["id"], "cutover_previous": current,
                "cutover_switched_at": switched_at, "cutover_wait_seconds": wait_seconds}

    def cutover_verify(self, state: PipelineState) -> dict[str, Any]:
        """설정 반영만으로 끝내지 않는다. 공개 리졸버가 blue 를 돌려주고, 서비스 도메인 응답이 blue 에서 오는지
        연속으로 확인한다. 그 뒤 green 을 TTL 만큼 유지하고, 그동안 레코드가 바뀌지 않았는지 다시 본다.
        확인하지 못하면 green 으로 되돌린다."""
        self._require_release(state)
        cfg, cf = self._cutover()
        url = f"{cfg.scheme}://{cfg.record}{cfg.verify_path}"
        keep_until = state["cutover_switched_at"] + state["cutover_wait_seconds"]
        deadline = keep_until + CUTOVER_GRACE_SECONDS
        streak, last, answer = 0, (0, ""), None
        while self._deps.now() < deadline:
            try:
                answer = self._deps.resolve(cfg.record)
                last = self._deps.probe(url) if answer == cfg.blue else (0, "")
            except ServiceUnavailable:
                last = (0, "")
            streak = streak + 1 if answer == cfg.blue and last[0] == 200 and last[1].startswith(cfg.expect_served_by) else 0
            if streak >= CUTOVER_STREAK:
                break
            self._deps.sleep(POLL_SECONDS)
        else:
            return self._rollback(state, cf, cfg, f"DNS 응답 {answer or '없음'}, HTTP {last[0]}, {last[1] or '식별값 없음'}")
        self._board.add_detail("cutover", verified={"url": url, "dns": answer, "served_by": last[1]})
        remaining = keep_until - self._deps.now()
        if remaining > 0:
            # green 을 남은 TTL 동안 유지한다(이전 응답을 캐시한 클라이언트가 아직 green 으로 온다)
            self._board.update("cutover", summary=f"blue 응답 확인. green 유지 {int(remaining)}초 대기")
            self._deps.sleep(remaining)
        final = self._read(lambda: cf.find_record(cfg.zone_id, cfg.record))
        if hostname(final.get("content")) != cfg.blue or final.get("proxied") is not False:
            raise Stopped("대기 중 DNS 설정이 달라졌다. green 을 유지한 채 사람이 확인해야 한다")
        self._board.update("cutover", state="pass",
                           summary=f"{cfg.record} → {cfg.blue}, 응답 {last[1]}. green 은 내리지 않음(TTL {state['cutover_wait_seconds']}초 경과)")
        return {"gates": {"cutover": "pass"}}

    def _rollback(self, state: PipelineState, cf: CloudflareClient, cfg: CutoverConfig, why: str) -> dict[str, Any]:
        try:
            cf.set_content(cfg.zone_id, state["cutover_record_id"], state["cutover_previous"])
            rolled = f"{state['cutover_previous']} 으로 되돌렸다"
        except ServiceUnavailable as exc:
            rolled = f"되돌리지 못했다({exc}). 사람이 바로 확인해야 한다"
        self._board.update("cutover", state="fail", summary=f"전환 후 검증 실패({why}). {rolled}")
        raise Stopped(f"전환 후 검증 실패: {rolled}")

    # ── 끝 ──────────────────────────────────────────────
    def done(self, state: PipelineState) -> dict[str, Any]:
        if self._deps.options.release_target is not None or self._deps.options.cutover:
            self._require_release(state)
        required = ("sast", "dast", "cutover") if self._deps.options.cutover else ("sast", "dast")
        gates = state.get("gates") or {}
        assert_gates_passed(gates, required)
        self._release()
        digest = state["image_digest"][:19]
        pr = state.get("fix_pr") or {}
        fixed = f"수정 후 재검증 · PR #{pr.get('number')} · " if pr.get("number") else "수정 후 재검증 · " if pr else ""
        # 사람이 승인해 넘긴 게이트는 '보안 통과'가 아니다. 끝까지 연결은 됐지만 검증 완료로 표시하지 않는다
        approved = [GATE_LABEL.get(g, g) for g in required if gates.get(g) == "approved"]
        if approved:
            self._board.finish("unresolved", f"{fixed}연결 확인 완료 · 보안 미해결({'·'.join(approved)} 사람 승인으로 진행) · {digest}",
                               result="warn")
        else:
            phase = "운영 배포 검증 완료" if self._deps.options.release_target is not None else "스테이징 검증 완료"
            self._board.finish("verified", f"{fixed}{phase} · {digest}", passed=True)
        return {"halt": None}

    def halted(self, state: PipelineState) -> dict[str, Any]:
        if state.get("deploy_build") is not None and not state.get("exception_cleanup_done"):
            self._release()
        self._board.finish("stopped", str(state.get("halt")))
        return {"halt": state.get("halt")}


    def onprem_start(self, state: PipelineState) -> dict[str, Any]:
        from delta_pipeline.onprem import start
        return start(self, state)

    def onprem_wait(self, state: PipelineState) -> dict[str, Any]:
        from delta_pipeline.onprem import wait
        result=wait(self, state)
        if (state.get('recovery_count') or state.get('manual_count')) and any(
                node['id']=='stage' and node['state']=='pass' for node in self._board.snapshot()['nodes']):
            self._board.update('recovery',state='pass',summary='같은 실행의 UID·이미지·HTTP·정리 재검사 완료')
        return result

def _rows_remaining(step: Mapping[str, Any] | None) -> int | None:
    """스테이징 도구의 data_reset 단계 내용("<Job>: 남은 행 N")에서 N 을 읽는다. 기록이 없으면 확인하지 못한 것이다."""
    found = ROWS_RE.search(str((step or {}).get("detail", "")))
    return int(found.group(1)) if found else None
