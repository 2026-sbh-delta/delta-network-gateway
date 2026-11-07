"""delta-pipeline 실행을 이 서버 안에서 돌린다. 환경별 한 번에 하나(GCP·온프레미스 병렬), 재시작하면 끝나지 못한 실행을 이어 간다.

환경 변수: ORCH_STATE_DIR, JENKINS_PLATFORM_PASSWORD, DELTA_SAST_TOKEN, DAST_PLATFORM_TOKEN, DELTA_STAGING_TOKEN(조회),
DELTA_IMAGE_REPO(선택), DELTA_INTERNAL_ZONE(선택),
DELTA_CUTOVER(선택, JSON: 서비스 도메인 전환 설정), CLOUDFLARE_API_TOKEN(선택: 해당 zone DNS 편집만),
DELTA_RELEASE_TARGETS(선택, JSON: 운영 대상 인벤토리 {target_id: 항목}), DELTA_RELEASE_SIGNING_KEY_B64(선택: Ed25519 PEM, base64),
DELTA_ECR_IMAGE_REPO(선택: 빌드 묶음의 ECR 사본 저장소), DELTA_AI_TOKEN(선택: 원인 진단 delta-ai)
DELTA_DIFF_REQUIRED(워크로드 차분 활성화), DELTA_DIFF_REQUEST_FILE(운영자 입력),
DELTA_DIFF_CONNECTION_PARAMETER 또는 DELTA_DIFF_URL/DELTA_DIFF_TOKEN(차분 서비스 연결).
"""

from __future__ import annotations

import base64
import json
import hashlib
from dataclasses import asdict
import os
import threading
import uuid
import time
from collections.abc import Callable
from pathlib import Path
from dataclasses import replace
from typing import Any

from delta_pipeline.diagnostic_client import DiagnosticClient
from delta_pipeline.diff_client import DiffClient, DiffInput, DiffJob
from delta_pipeline import domain
from delta_pipeline.clients import Cloudflare, Dast, DeltaAi, Jenkins, Sast, Staging, ServiceUnavailable, probe, resolve_cname
from delta_pipeline.model import Options, REPO_PREFIX
from delta_pipeline.run import BOARD_DIR, Run, unfinished_runs
from delta_pipeline.steps import CutoverConfig

IMAGE_REPO = "asia-northeast3-docker.pkg.dev/term2-delta/delta-lab-staging/delta-sample-app"
RunFactory = Callable[[Options, Path, "str | None"], Run]


def cutover_config() -> CutoverConfig | None:
    raw = os.environ.get("DELTA_CUTOVER")
    return CutoverConfig(**json.loads(raw)) if raw else None


def release_targets() -> dict[str, Any]:
    """운영 대상 인벤토리(비밀값 없음). 화면의 대상 선택과 승인 설정 해시의 기준이다."""
    raw = os.environ.get("DELTA_RELEASE_TARGETS")
    value = json.loads(raw) if raw else {}
    return value if isinstance(value, dict) else {}


def release_signing_key() -> bytes | None:
    """운영 승인 서명 개인키(Ed25519 PEM). env 파일 한 줄에 넣으려고 base64 로 감싼 값을 푼다."""
    raw = os.environ.get("DELTA_RELEASE_SIGNING_KEY_B64", "")
    return base64.b64decode(raw) if raw else None


def diff_required() -> bool:
    return os.environ.get("DELTA_DIFF_REQUIRED") == "1"


def diff_input() -> DiffInput:
    return DiffInput.from_file(os.environ.get("DELTA_DIFF_REQUEST_FILE", ""))


def diff_client() -> DiffClient:
    parameter = os.environ.get("DELTA_DIFF_CONNECTION_PARAMETER")
    if parameter:
        if parameter != "/delta-cp/shared/diff_connection":
            raise ValueError("고정된 차분 연결 파라미터가 필요합니다")
        import boto3
        from botocore.config import Config

        client = boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "ap-northeast-2"),
                              config=Config(connect_timeout=3, read_timeout=5, retries={"total_max_attempts": 1}))
        secret = client.get_parameter(Name=parameter, WithDecryption=True)["Parameter"]["Value"]
        connection = json.loads(secret)
        url, token, ca = connection["url"], connection["token"], connection.get("ca_base64")
    else:
        url, token, ca = (os.environ.get(key) for key in ("DELTA_DIFF_URL", "DELTA_DIFF_TOKEN", "DELTA_DIFF_CA_BASE64"))
    return DiffClient(url or "", token or "", ca,
                      allow_private_http=os.environ.get("DELTA_DIFF_ALLOW_PRIVATE_HTTP") == "1")


def platform_run(options: Options, store_dir: Path, run_id: str | None) -> Run:
    zone = os.environ.get("DELTA_INTERNAL_ZONE", "cp.internal")
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    jenkins = f"http://jenkins.{zone}:8080"
    password = os.environ.get("JENKINS_PLATFORM_PASSWORD", "")
    diagnostic = None
    evidence = None
    if os.environ.get("DELTA_DIAGNOSTIC_URL") or os.environ.get("DELTA_DIAGNOSTICS_REQUIRED") == "1":
        try:
            diagnostic = DiagnosticClient(os.environ["DELTA_DIAGNOSTIC_URL"],
                os.environ["DELTA_DIAGNOSTIC_TOKEN"], os.environ.get("DELTA_DIAGNOSTIC_CA_BASE64"))
            options = replace(options, diagnostics=True)
        except Exception:
            raise ServiceUnavailable("진단 서비스 연결 설정을 확인하세요") from None
    if os.environ.get("DELTA_LOG_EVIDENCE_ENABLED") == "1":
        from delta_pipeline.cloudwatch_evidence import CloudWatchEvidence
        evidence = CloudWatchEvidence(store_dir / "log-evidence")
    if options.execution_cloud == 'onprem':
        if os.environ.get('DELTA_ONPREM_ENABLED')!='1' or not options.onprem_build_id:
            raise ServiceUnavailable('온프레미스 연결과 등록된 빌드를 확인하세요')
        from delta_pipeline.onprem_ssm import WorkerRpc,RemoteOnprem,RemoteSast
        from delta_pipeline.registered_build import RegisteredBuild
        rpc=WorkerRpc();value=rpc.call('context',build_id=options.onprem_build_id)
        artifact=RegisteredBuild.from_snapshot(value['build'],service=value['service'])
        artifact.assert_request(options.repo,options.commit)
        run_id=run_id or uuid.uuid4().hex
        return Run(options,run_id=run_id,store_dir=store_dir,build_jenkins=artifact,deploy_jenkins=artifact,
            sast=RemoteSast(rpc,artifact.build_id),
            dast=Dast(f'http://dast.{zone}:8765',os.environ.get('DAST_PLATFORM_TOKEN','')),
            staging=Staging(f'http://jenkins.{zone}:8091',os.environ.get('DELTA_STAGING_TOKEN','')),
            image_repo=artifact.image.rsplit('@',1)[0],diagnostic=diagnostic,evidence=evidence,
            onprem=RemoteOnprem(rpc,artifact.build_id,graph_id=run_id,state_dir=store_dir))
    diff = None
    if options.diff_request_hash is not None:
        run_id = run_id or uuid.uuid4().hex
        try:
            request = diff_input()
            if (diagnostic is not None and request.fingerprint == options.diff_request_hash
                    and options.repo == REPO_PREFIX + request.service_id):
                diff = DiffJob(diff_client(), request, run_id)
        except Exception:
            # The graph stops and records a diagnostic without exposing a token, file or AWS error.
            pass
    return Run(options,
               sast=Sast(f"http://sast.{zone}:8790", os.environ.get("DELTA_SAST_TOKEN", "")),
               # Host 가 DAST_INTERNAL_HOSTS(dast.cp.internal:8765)와 맞아야 플랫폼 토큰이 통한다
               dast=Dast(f"http://dast.{zone}:8765", os.environ.get("DAST_PLATFORM_TOKEN", "")),
               build_jenkins=Jenkins(jenkins, "platform", password, job="delta-build"),
               deploy_jenkins=Jenkins(jenkins, "platform", password, job="delta-deploy"),
               # 조회 토큰: 점검을 끝낸 뒤 자기 스테이징 점유를 푸는 데만 쓴다(배포·초기화는 못 한다)
               staging=Staging(f"http://jenkins.{zone}:8091", os.environ.get("DELTA_STAGING_TOKEN", "")),
               image_repo=os.environ.get("DELTA_IMAGE_REPO", IMAGE_REPO), store_dir=store_dir, run_id=run_id,
               diagnostic=diagnostic, evidence=evidence,
               diff=diff, require_diff_cutover=diff_required(),
               cloudflare=Cloudflare(token) if token else None, cutover=cutover_config() if options.cutover else None,
               # 운영 배포(환경별 VM 생성)는 사람 승인 서명 뒤 Jenkins delta-release 가 한다
               release_jenkins=Jenkins(jenkins, "platform", password, job="delta-release") if options.release_target else None,
               release_signing_key=release_signing_key(),
               release_config=release_targets().get(options.release_target) if options.release_target else None,
               ecr_image_repo=os.environ.get("DELTA_ECR_IMAGE_REPO", ""),
               # 멈춘 실행의 원인 진단(연재님 delta-ai). 판정은 바꾸지 않는다
               diagnose=DeltaAi(f"http://ai.{zone}:8090", os.environ["DELTA_AI_TOKEN"]).assess
               if os.environ.get("DELTA_AI_TOKEN") else None)


class Pipelines:
    def __init__(self, store_dir: str | Path, factory: RunFactory = platform_run) -> None:
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self._factory = factory
        self._lock = threading.RLock()
        self.run: Run | None = None
        self._thread: threading.Thread | None = None
        self._runs: dict[str, Run] = {}
        self._threads: dict[str, threading.Thread] = {}
        saved = unfinished_runs(self.store_dir) if (self.store_dir / BOARD_DIR).exists() else []
        for run_id, options in saved:
            # Resume one unfinished execution per environment; never replay two
            # jobs against the same staging resources.
            if not self.running(options.execution_cloud):
                self._launch(options, run_id)

    def _launch(self, options: Options, run_id: str | None = None) -> str:
        run = self._factory(options, self.store_dir, run_id)
        thread = threading.Thread(target=run.execute, daemon=True)
        self._runs[run.run_id], self._threads[run.run_id] = run, thread
        self.run, self._thread = run, thread  # Compatibility for older callers.
        thread.start()
        return run.run_id

    def active_ids(self) -> list[str]:
        with self._lock:
            return [rid for rid, thread in self._threads.items() if thread.is_alive()]

    def running(self, cloud: str | None = None) -> bool:
        with self._lock:
            return any(self._threads[rid].is_alive() and
                       (cloud is None or run.options.execution_cloud == cloud)
                       for rid, run in self._runs.items())

    def cancel(self, run_id: str) -> bool:
        with self._lock:
            run = self._runs.get(run_id)
            return bool(run and self._threads[run_id].is_alive() and run.cancel())

    def start(self, repo: str, commit: str, *, cutover: bool = False, branch: str = "", execution_cloud='gcp', onprem_build_id=None, assistance=False, release_target=None, request_key=None) -> str:
        with self._lock:
            options = Options(repo, commit, cutover=cutover, branch=branch, execution_cloud=execution_cloud,
                              onprem_build_id=onprem_build_id, assistance=assistance, release_target=release_target)
            if execution_cloud == "gcp" and diff_required():
                request = diff_input()
                if repo == REPO_PREFIX + request.service_id:
                    options = replace(options, diff_request_hash=request.fingerprint)
                elif cutover:
                    raise RuntimeError("이 서비스에 고정된 Diff 입력이 없어 도메인 전환을 할 수 없습니다")
            run_id = hashlib.sha256(("platform:" + request_key).encode()).hexdigest() if request_key else None
            if run_id and (old := self.get(run_id)):
                expected = asdict(options)
                actual = dict(old["options"])
                actual.setdefault("diff_request_hash", None)
                expected.pop("diagnostics", None)
                actual.pop("diagnostics", None)
                if actual != expected:
                    raise RuntimeError("같은 요청 키에 다른 실행 입력을 사용할 수 없습니다")
                return run_id
            if self.running(execution_cloud):
                raise RuntimeError("해당 환경에 이미 실행 중인 파이프라인이 있습니다")
            return self._launch(options, run_id)

    def answer(self, run_id: str, value: str, case_id: str | None = None, *, decision_id: str | None = None) -> bool:
        with self._lock:
            run = self._runs.get(run_id)
            if not run:
                return False
            if decision_id is not None:
                return bool(run.answer(value, case_id, decision_id=decision_id))
            return bool(run.answer(value, case_id))

    def get(self, run_id: str) -> dict[str, Any] | None:
        run = getattr(self, "_runs", {}).get(run_id)
        if run is not None:
            return run.snapshot()
        path = self.store_dir / BOARD_DIR / f"{run_id}.json"
        if not run_id.isalnum() or not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        boards = sorted((self.store_dir / BOARD_DIR).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        out = []
        for path in boards[:limit]:
            saved = json.loads(path.read_text(encoding="utf-8"))
            # built: 이 커밋이 빌드까지 됐는지. 시작 화면이 마지막으로 빌드된 브랜치를 기본으로 고르는 데 쓴다
            built = any(n["id"] == "build" and n["state"] == "pass" for n in saved.get("nodes", ()))
            out.append({k: saved.get(k) for k in ("run_id", "options", "outcome", "started_at")} | {"built": built})
        return out


class DomainRevert:
    """서비스 도메인 되돌리기(시연 초기화). 화면에서 사람이 승인하면 시작한다. 한 번에 하나, 결과는 메모리에만 둔다."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.status: dict[str, Any] = {"state": "idle"}

    def configured(self) -> tuple[CutoverConfig, Cloudflare] | None:
        cfg, token = cutover_config(), os.environ.get("CLOUDFLARE_API_TOKEN")
        return (cfg, Cloudflare(token)) if cfg and token else None

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def view(self) -> dict[str, Any]:
        found = self.configured()
        if found is None:
            return {"configured": False}
        cfg, cf = found
        try:
            target = domain.current_target(cfg, cf)
        except Exception:  # noqa: BLE001 조회 실패는 화면에 "확인 불가"로만 보인다
            target = None
        return {"configured": True, "record": cfg.record, "green": cfg.green, "blue": cfg.blue,
                "target": target, "revert": self.status}

    def start(self) -> None:
        found = self.configured()
        if found is None:
            raise RuntimeError("서비스 도메인 전환 설정·토큰이 없습니다")
        with self._lock:
            if self.running():
                raise RuntimeError("되돌리기가 이미 진행 중입니다")
            cfg, cf = found
            self.status = {"state": "running", "summary": "시작", "started_at": time.time()}

            def report(update: dict[str, Any]) -> None:
                self.status = {**self.status, **update}

            def work() -> None:
                try:
                    domain.revert(cfg, cf, probe=probe, resolve=resolve_cname, now=time.time, sleep=time.sleep,
                                  report=report)
                except Exception as exc:  # noqa: BLE001 예상 밖 실패도 화면에 남긴다
                    report({"state": "fail", "summary": f"되돌리기 중 오류({type(exc).__name__})"})

            self._thread = threading.Thread(target=work, daemon=True)
            self._thread.start()
