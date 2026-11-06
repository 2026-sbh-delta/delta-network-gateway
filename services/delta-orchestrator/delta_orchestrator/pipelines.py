"""delta-pipeline 실행을 이 서버 안에서 돌린다. 한 번에 하나(스테이징은 하나뿐), 재시작하면 끝나지 못한 실행을 이어 간다.

환경 변수: ORCH_STATE_DIR, JENKINS_PLATFORM_PASSWORD, DELTA_SAST_TOKEN, DAST_PLATFORM_TOKEN, DELTA_STAGING_TOKEN(조회),
DELTA_IMAGE_REPO(선택), DELTA_INTERNAL_ZONE(선택),
DELTA_CUTOVER(선택, JSON: 서비스 도메인 전환 설정), CLOUDFLARE_API_TOKEN(선택: 해당 zone DNS 편집만),
DELTA_RELEASE_TARGETS(선택, JSON: 운영 대상 인벤토리 {target_id: 항목}), DELTA_RELEASE_SIGNING_KEY_B64(선택: Ed25519 PEM, base64),
DELTA_ECR_IMAGE_REPO(선택: 빌드 묶음의 ECR 사본 저장소), DELTA_AI_TOKEN(선택: 원인 진단 delta-ai)
"""

from __future__ import annotations

import base64
import json
import os
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from dataclasses import replace
from typing import Any

from delta_pipeline.diagnostic_client import DiagnosticClient
from delta_pipeline.clients import Cloudflare, Dast, DeltaAi, Jenkins, Sast, Staging, ServiceUnavailable
from delta_pipeline.model import Options
from delta_pipeline.run import BOARD_DIR, Run, latest_unfinished
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
        self._lock = threading.Lock()
        self.run: Run | None = None
        self._thread: threading.Thread | None = None
        saved = latest_unfinished(self.store_dir) if (self.store_dir / BOARD_DIR).exists() else None
        if saved:
            self._launch(saved[1], saved[0])

    def _launch(self, options: Options, run_id: str | None = None) -> str:
        self.run = self._factory(options, self.store_dir, run_id)
        self._thread = threading.Thread(target=self.run.execute, daemon=True)
        self._thread.start()
        return self.run.run_id

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, repo: str, commit: str, *, cutover: bool = False, branch: str = "", execution_cloud='gcp', onprem_build_id=None, assistance=False, release_target=None) -> str:
        with self._lock:
            if self.running():
                raise RuntimeError("이미 실행 중인 파이프라인이 있습니다(스테이징은 하나뿐)")
            return self._launch(Options(repo, commit, cutover=cutover, branch=branch,
                execution_cloud=execution_cloud,onprem_build_id=onprem_build_id,assistance=assistance,release_target=release_target))

    def answer(self, run_id: str, value: str, case_id: str | None = None) -> bool:
        return bool(self.run and self.run.run_id == run_id and self.run.answer(value, case_id))

    def get(self, run_id: str) -> dict[str, Any] | None:
        if self.run and self.run.run_id == run_id:
            return self.run.snapshot()
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
