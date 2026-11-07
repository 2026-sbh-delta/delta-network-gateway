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

    def start(self, repo: str, commit: str, *, cutover: bool = False, branch: str = "", execution_cloud='gcp', onprem_build_id=None, assistance=False, release_target=None, request_key=None, project_id=None) -> str:
        with self._lock:
            options = Options(repo, commit, cutover=cutover, branch=branch, execution_cloud=execution_cloud,
                              onprem_build_id=onprem_build_id, assistance=assistance, release_target=release_target,
                              project_id=project_id)
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
                actual.setdefault("project_id", None)
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


class DemoReset:
    """시연 초기화. 화면에서 사람이 승인하면 시작하고, 파이프라인이 돌고 있으면 시작하지 않는다.

    1) 서비스 도메인을 AWS 로 되돌린다  2) GCP 운영에 취약 버전(수정 전 main 빌드)을 다시 배포한다
    3) AI 수정 PR·브랜치(dast/fix-*)를 정리한다. main 은 처음부터 취약 버전이라 건드리지 않는다.
    단계가 실패해도 다음 단계는 계속하고, 결과는 단계별로 화면에 남긴다(메모리에만 둔다).
    """

    STEPS = (("domain", "도메인 AWS 복귀"), ("release", "GCP 운영 취약 버전 재배포"), ("fixes", "AI 수정 PR 정리"))
    RELEASE_DEADLINE_SECONDS = 1800

    def __init__(self, pipelines: Pipelines, revert: DomainRevert) -> None:
        self._pipelines, self._revert = pipelines, revert
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.status: dict[str, Any] = {"state": "idle", "steps": self._fresh()}

    def _fresh(self) -> list[dict[str, Any]]:
        return [{"id": key, "label": label, "state": "pending", "summary": ""} for key, label in self.STEPS]

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def view(self) -> dict[str, Any]:
        return self.status

    def _step(self, key: str, **update: Any) -> None:
        self.status = {**self.status, "steps": [{**s, **update} if s["id"] == key else s
                                                  for s in self.status["steps"]]}

    def latest_repo(self) -> str:
        """정리할 저장소: 가장 최근 실행의 저장소(빌드 기록이 없어도 PR 정리는 한다)."""
        boards = sorted((self._pipelines.store_dir / BOARD_DIR).glob("*.json"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
        for path in boards:
            repo = (json.loads(path.read_text(encoding="utf-8")).get("options") or {}).get("repo")
            if repo:
                return repo
        raise RuntimeError("정리할 저장소를 찾지 못했습니다")

    @staticmethod
    def branch_head(repo: str, branch: str) -> str:
        """공개 저장소 브랜치의 현재 커밋. 시연 앱의 main 은 취약 버전 그대로다(AI 수정은 PR 로만 간다)."""
        import urllib.request

        name = repo.removeprefix("https://github.com/").removesuffix(".git").strip("/")
        request = urllib.request.Request(f"https://api.github.com/repos/{name}/commits/{branch}",
                                         headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)["sha"]

    def vulnerable_bundle(self, head: Callable[[str, str], str] | None = None) -> tuple[dict[str, Any], str]:
        """main 현재 커밋을 수정 없이 빌드한 가장 최근 묶음. 예전 커밋 빌드는 고르지 않는다."""
        from delta_pipeline.delivery import validate_bundle

        head = head or self.branch_head
        heads: dict[tuple[str, str], str] = {}
        boards = sorted((self._pipelines.store_dir / BOARD_DIR).glob("*.json"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
        for path in boards:
            saved = json.loads(path.read_text(encoding="utf-8"))
            options = saved.get("options") or {}
            # 시연 앱은 main 이 취약 버전이다. 다른 브랜치(작업 브랜치)의 빌드는 고르지 않는다.
            if (options.get("execution_cloud", "gcp") != "gcp" or not options.get("repo")
                    or (options.get("branch") or "main") != "main"):
                continue
            build = next((n for n in saved.get("nodes", ()) if n.get("id") == "build"), {})
            bundle = (build.get("detail") or {}).get("bundle")
            if build.get("state") != "pass" or not bundle or bundle.get("commit") != options.get("commit"):
                continue
            key = (options["repo"], "main")
            if key not in heads:
                heads[key] = head(*key)
            if options["commit"] != heads[key]:
                continue
            try:
                return validate_bundle(bundle, options["commit"]), options["repo"]
            except (ValueError, KeyError):
                continue
        raise RuntimeError("main 현재 커밋을 수정 없이 빌드한 기록이 없습니다. 파이프라인을 한 번 돌려 빌드를 남기세요")

    def build_main(self, jenkins: Any = None) -> tuple[dict[str, Any], str]:
        """main 현재 커밋(취약 버전 그대로)을 파이프라인과 같은 delta-build 로 빌드해 묶음을 얻는다."""
        from delta_pipeline.delivery import validate_bundle

        repo = self.latest_repo()
        commit = self.branch_head(repo, "main")
        if jenkins is None:
            zone = os.environ.get("DELTA_INTERNAL_ZONE", "cp.internal")
            jenkins = Jenkins(f"http://jenkins.{zone}:8080", "platform",
                              os.environ.get("JENKINS_PLATFORM_PASSWORD", ""), job="delta-build")
        build = jenkins.trigger({"REPO": repo, "COMMIT": commit,
                                 "IMAGE_REPO": os.environ.get("DELTA_IMAGE_REPO", IMAGE_REPO)},
                                f"reset-build-{uuid.uuid4().hex[:20]}")
        self._step("release", summary=f"main {commit[:7]} 빌드 중 (Jenkins delta-build #{build})")
        deadline = time.time() + self.RELEASE_DEADLINE_SECONDS
        while time.time() < deadline:
            status = jenkins.status(build)
            if status != "IN_PROGRESS":
                result = jenkins.file(build, "result.json") or {}
                if status != "SUCCESS":
                    raise RuntimeError(f"main 빌드 실패 (#{build} {status})")
                return validate_bundle(result, commit), repo
            time.sleep(5)
        raise RuntimeError(f"main 빌드 #{build} 가 제한 시간 안에 끝나지 않았습니다")

    def _redeploy(self) -> str:
        from delta_pipeline.clients import evidence_hash
        from delta_pipeline.delivery import canonical_json, sign_approval, validate_config

        try:
            bundle, repo = self.vulnerable_bundle()
        except RuntimeError:
            # A run that went through the AI fix keeps only the fixed rebuild, so build main here.
            bundle, repo = self.build_main()
        self._repo = repo
        target_id, config = next(((k, v) for k, v in release_targets().items() if v.get("cloud") == "gcp"),
                                 (None, None))
        if target_id is None:
            raise RuntimeError("GCP 운영 대상이 없습니다")
        config = validate_config(target_id, config)
        key = release_signing_key()
        deployment_id = f"reset-{uuid.uuid4().hex[:24]}"
        claims = {"deployment_id": deployment_id, "commit_sha": bundle["commit"],
                  "bundle_sha256": bundle["bundle_sha256"], "target_id": target_id, "environment": "production",
                  "config_sha256": evidence_hash(config), "exp": int(time.time()) + 600, "nonce": uuid.uuid4().hex}
        approval = sign_approval(claims, key)
        images = (bundle.get("registry_images") or {}).get("gcp") or bundle["images"]
        zone = os.environ.get("DELTA_INTERNAL_ZONE", "cp.internal")
        jenkins = Jenkins(f"http://jenkins.{zone}:8080", "platform", os.environ.get("JENKINS_PLATFORM_PASSWORD", ""),
                          job="delta-release")
        build = jenkins.trigger({"TARGET_ID": target_id, "COMMIT": bundle["commit"],
                                 "BUNDLE_SHA256": bundle["bundle_sha256"], "IMAGES_JSON": canonical_json(images),
                                 "APPROVAL": canonical_json(approval),
                                 "BUNDLE_JSON": canonical_json({**bundle, "schema": 2, "platform": "linux/amd64"})},
                                deployment_id)
        self._step("release", summary=f"{target_id} · {bundle['commit'][:7]} 배포 중 (Jenkins #{build})")
        deadline = time.time() + self.RELEASE_DEADLINE_SECONDS
        while time.time() < deadline:
            status = jenkins.status(build)
            if status != "IN_PROGRESS":
                result = jenkins.file(build, "result.json") or {}
                if status != "SUCCESS" or result.get("state") != "verified":
                    raise RuntimeError(f"Jenkins #{build} {status} {result.get('code') or ''}".strip())
                return f"{target_id} · {bundle['commit'][:7]} 취약 버전 검증 완료"
            time.sleep(10)
        raise RuntimeError(f"Jenkins #{build} 가 제한 시간 안에 끝나지 않았습니다")

    def start(self) -> None:
        with self._lock:
            if self.running():
                raise RuntimeError("초기화가 이미 진행 중입니다")
            if self._pipelines.running():
                raise RuntimeError("파이프라인 실행 중에는 초기화하지 않습니다")
            self.status = {"state": "running", "started_at": time.time(), "steps": self._fresh()}
            self._repo = ""

            def work() -> None:
                failed = False
                self._step("domain", state="running", summary="AWS 입구 확인 중")
                found = self._revert.configured()
                if found is None:
                    self._step("domain", state="skipped", summary="도메인 전환 설정 없음")
                else:
                    cfg, cf = found
                    try:
                        result = domain.revert(cfg, cf, probe=probe, resolve=resolve_cname, now=time.time,
                                               sleep=time.sleep, report=lambda u: self._step("domain", summary=u.get("summary", "")))
                    except Exception as exc:  # noqa: BLE001 화면에 남긴다
                        result = {"state": "fail", "summary": f"오류({type(exc).__name__})"}
                    failed |= result["state"] != "pass"
                    self._step("domain", state=result["state"], summary=result["summary"])
                self._step("release", state="running", summary="수정 전 빌드 찾는 중")
                try:
                    self._step("release", state="pass", summary=self._redeploy())
                except Exception as exc:  # noqa: BLE001
                    failed = True
                    self._step("release", state="fail", summary=str(exc)[:200] if isinstance(exc, RuntimeError)
                               else f"오류({type(exc).__name__})")
                self._step("fixes", state="running", summary="수정 PR 닫는 중")
                try:
                    repo = self._repo or self.latest_repo()
                    zone = os.environ.get("DELTA_INTERNAL_ZONE", "cp.internal")
                    done = Dast(f"http://dast.{zone}:8765", os.environ.get("DAST_PLATFORM_TOKEN", "")) \
                        .cleanup_fix_branches(repo)
                    self._step("fixes", state="pass",
                               summary=f"PR {done.get('closed_prs', 0)}개 닫음 · 브랜치 {done.get('deleted_branches', 0)}개 삭제")
                except Exception as exc:  # noqa: BLE001
                    failed = True
                    self._step("fixes", state="fail", summary=f"오류({type(exc).__name__})")
                self.status = {**self.status, "state": "fail" if failed else "pass", "ended_at": time.time()}

            self._thread = threading.Thread(target=work, daemon=True)
            self._thread.start()
