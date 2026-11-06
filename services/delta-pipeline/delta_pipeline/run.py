"""실행 한 번을 구동한다. 사람 확인을 기다리고, 강제 종료 뒤에는 체크포인트에서 이어 간다."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from delta_pipeline.clients import ServiceUnavailable
from delta_pipeline.graph import build_graph
from delta_pipeline.model import Board, Options
from delta_pipeline.steps import CloudflareClient, CutoverConfig, DastClient, Deps, JenkinsClient, SastClient, StagingClient

DECISION_DEADLINE_SECONDS = 1800
DECISION_POLL_SECONDS = 0.5
CHECKPOINT_FILE = "checkpoints.sqlite"
BOARD_DIR = "runs"
LOG = logging.getLogger(__name__)


def latest_unfinished(store_dir: Path) -> tuple[str, Options] | None:
    """강제 종료로 끝나지 못한 가장 최근 실행을 찾는다."""
    boards = sorted((store_dir / BOARD_DIR).glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in boards:
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved["outcome"] is None and "options" in saved:
            return saved["run_id"], Options(**saved["options"])
    return None


class Run:
    def __init__(self, options: Options, *, sast: SastClient, dast: DastClient, build_jenkins: JenkinsClient,
                 deploy_jenkins: JenkinsClient, staging: StagingClient, image_repo: str, store_dir: Path,
                 sleep: Callable[[float], None] = time.sleep, run_id: str | None = None,
                 cloudflare: CloudflareClient | None = None, cutover: CutoverConfig | None = None,
                 probe: Callable[..., tuple[int, str]] | None = None,
                 resolve: Callable[[str], str | None] | None = None, now: Callable[[], float] | None = None,
                 release_jenkins: JenkinsClient | None = None, release_signing_key: bytes | None = None,
                 release_config: dict[str, Any] | None = None,
                 diagnostic: Any = None, report_dir: Path | None = None, evidence: Any = None) -> None:
        self.run_id = run_id or uuid.uuid4().hex
        if diagnostic is not None:
            options = replace(options, diagnostics=True)
        self.options = options
        (store_dir / BOARD_DIR).mkdir(parents=True, exist_ok=True)
        self._board = Board(store_dir / BOARD_DIR / f"{self.run_id}.json", options, self.run_id)
        self._store_dir = store_dir
        self._deps = Deps(run_id=self.run_id, options=options, board=self._board, sast=sast, dast=dast,
                          build_jenkins=build_jenkins, deploy_jenkins=deploy_jenkins, staging=staging,
                          image_repo=image_repo, sleep=sleep, cloudflare=cloudflare, cutover=cutover,
                          diagnostic=diagnostic, evidence=evidence, report_dir=report_dir or store_dir / "reports",
                          release_jenkins=release_jenkins, release_signing_key=release_signing_key, release_config=release_config,
                          **{k: v for k, v in (("probe", probe), ("resolve", resolve), ("now", now)) if v})
        self._waiting = False
        self._waiting_case = None
        self._answer_lock = threading.Lock()
        self._answer: str | None = None
        self._answered = threading.Event()

    def snapshot(self) -> dict[str, Any]:
        return self._board.snapshot()

    def answer(self, value: str, case_id: str | None = None) -> bool:
        with self._answer_lock:
            if not self._waiting or self._answered.is_set() or value not in self._board.waiting_options():
                return False
            if self._waiting_case is not None and case_id != self._waiting_case:
                return False
            self._answer = value
            self._answered.set()
            return True

    def execute(self) -> None:
        connection = sqlite3.connect(self._store_dir / CHECKPOINT_FILE, check_same_thread=False)
        try:
            self._drive(build_graph(self._deps, SqliteSaver(connection)))
        except TimeoutError as exc:
            self._board.finish("stopped", str(exc))
        except ServiceUnavailable as exc:
            self._board.finish("stopped", f"서비스 응답 없음, 통과로 보지 않는다: {exc}")
        except Exception:
            # 체크포인트는 남아 있다. 어느 노드에서 죽었는지는 로그로 본다
            LOG.exception("실행 %s 이 예상하지 못한 오류로 멈췄다", self.run_id)
            self._board.finish("stopped", "내부 오류로 멈췄다. 통과로 보지 않는다")
        finally:
            connection.close()

    def _drive(self, graph: Any) -> None:
        config = {"configurable": {"thread_id": self.run_id}}
        current = graph.get_state(config)
        command: Any = {} if not current.values and not current.next else None
        while True:
            pending = self._pending_interrupt(graph.get_state(config))
            if pending is not None:
                command = Command(resume=self._wait_for_answer(pending))
            elif command is None and not graph.get_state(config).next:
                return
            # 기본값은 비동기 저장이라 강제 종료 때 끝난 노드가 다시 돈다(배포가 두 번 시작될 수 있다)
            for _ in graph.stream(command, config, durability="sync"):
                pass
            command = None

    @staticmethod
    def _pending_interrupt(state: Any) -> Mapping[str, Any] | None:
        for task in state.tasks:
            for item in task.interrupts:
                return item.value
        return None

    def _wait_for_answer(self, request: Mapping[str, Any]) -> str:
        """사람의 답을 기다린다. 답이 없으면 진행하지 않는다."""
        node_id = request["node_id"]
        self._answered.clear()
        self._waiting_case = request.get("case_id")
        self._waiting = True
        self._board.update(node_id, state="waiting", decision={
            "prompt": request["prompt"], "case_id": request.get("case_id"), "options": [{"value": value, "label": label} for value, label in request["options"]]})
        deadline = time.time() + DECISION_DEADLINE_SECONDS
        try:
            while time.time() < deadline:
                if self._answered.wait(timeout=DECISION_POLL_SECONDS):
                    return self._answer or "stop"
            raise TimeoutError("사람 확인 시간이 지났다")
        finally:
            self._waiting = False
            self._waiting_case = None
            self._board.update(node_id, decision=None)
