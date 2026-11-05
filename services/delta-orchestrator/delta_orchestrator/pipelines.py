"""delta-pipeline 실행을 이 서버 안에서 돌린다. 한 번에 하나(스테이징은 하나뿐), 재시작하면 끝나지 못한 실행을 이어 간다.

환경 변수: ORCH_STATE_DIR, JENKINS_PLATFORM_PASSWORD, DELTA_SAST_TOKEN, DAST_PLATFORM_TOKEN, DELTA_STAGING_TOKEN(조회),
DELTA_IMAGE_REPO(선택), DELTA_INTERNAL_ZONE(선택),
DELTA_CUTOVER(선택, JSON: 서비스 도메인 전환 설정), CLOUDFLARE_API_TOKEN(선택: 해당 zone DNS 편집만)
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from delta_pipeline.clients import Cloudflare, Dast, Jenkins, Sast, Staging
from delta_pipeline.model import Options
from delta_pipeline.run import BOARD_DIR, Run, latest_unfinished
from delta_pipeline.steps import CutoverConfig

IMAGE_REPO = "asia-northeast3-docker.pkg.dev/term2-delta/delta-lab-staging/delta-sample-app"
RunFactory = Callable[[Options, Path, "str | None"], Run]


def cutover_config() -> CutoverConfig | None:
    raw = os.environ.get("DELTA_CUTOVER")
    return CutoverConfig(**json.loads(raw)) if raw else None


def platform_run(options: Options, store_dir: Path, run_id: str | None) -> Run:
    zone = os.environ.get("DELTA_INTERNAL_ZONE", "cp.internal")
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    jenkins = f"http://jenkins.{zone}:8080"
    password = os.environ.get("JENKINS_PLATFORM_PASSWORD", "")
    return Run(options,
               sast=Sast(f"http://sast.{zone}:8790", os.environ.get("DELTA_SAST_TOKEN", "")),
               # Host 가 DAST_INTERNAL_HOSTS(dast.cp.internal:8765)와 맞아야 플랫폼 토큰이 통한다
               dast=Dast(f"http://dast.{zone}:8765", os.environ.get("DAST_PLATFORM_TOKEN", "")),
               build_jenkins=Jenkins(jenkins, "platform", password, job="delta-build"),
               deploy_jenkins=Jenkins(jenkins, "platform", password, job="delta-deploy"),
               # 조회 토큰: 점검을 끝낸 뒤 자기 스테이징 점유를 푸는 데만 쓴다(배포·초기화는 못 한다)
               staging=Staging(f"http://jenkins.{zone}:8091", os.environ.get("DELTA_STAGING_TOKEN", "")),
               image_repo=os.environ.get("DELTA_IMAGE_REPO", IMAGE_REPO), store_dir=store_dir, run_id=run_id,
               cloudflare=Cloudflare(token) if token else None, cutover=cutover_config() if options.cutover else None)


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

    def start(self, repo: str, commit: str, *, cutover: bool = False, branch: str = "") -> str:
        with self._lock:
            if self.running():
                raise RuntimeError("이미 실행 중인 파이프라인이 있습니다(스테이징은 하나뿐)")
            return self._launch(Options(repo, commit, cutover=cutover, branch=branch))

    def answer(self, run_id: str, value: str) -> bool:
        return bool(self.run and self.run.run_id == run_id and self.run.answer(value))

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
            out.append({k: saved.get(k) for k in ("run_id", "options", "outcome", "started_at")})
        return out
