"""실행 한 번을 구동한다. 사람 확인을 기다리고, 강제 종료 뒤에는 체크포인트에서 이어 간다."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from delta_pipeline.clients import ServiceUnavailable
from delta_pipeline.graph import build_graph
from delta_pipeline.model import SCENARIOS, Board, Options
from delta_pipeline.steps import ELSEWHERE, DastClient, DeltaClient, Deps, JenkinsClient

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
        scenario = next((s for s in SCENARIOS if s.id == saved["scenario"]["id"]), None)
        if saved["outcome"] is None and scenario is not None:
            return saved["run_id"], Options(scenario, saved["planner"], saved["rejudge_onprem"])
    return None


class Run:
    def __init__(self, options: Options, *, delta: DeltaClient, dast: DastClient, jenkins: JenkinsClient,
                 onprem_dir: Path, store_dir: Path, service_ready: Callable[[], bool],
                 on_release_start: Callable[[], None] = lambda: None, sleep: Callable[[float], None] = time.sleep,
                 run_id: str | None = None) -> None:
        self.run_id = run_id or uuid.uuid4().hex
        self.options = options
        self._jenkins = jenkins
        (store_dir / BOARD_DIR).mkdir(parents=True, exist_ok=True)
        self._board = Board(store_dir / BOARD_DIR / f"{self.run_id}.json", options, self.run_id)
        self._store_dir = store_dir
        self._deps = Deps(run_id=self.run_id, options=options, board=self._board, delta=delta, dast=dast, jenkins=jenkins,
                          onprem_dir=onprem_dir, service_ready=service_ready, on_release_start=on_release_start, sleep=sleep)
        self._answer: str | None = None
        self._answered = threading.Event()

    def snapshot(self) -> dict[str, Any]:
        return self._board.snapshot()

    def answer(self, value: str) -> bool:
        if value not in self._board.waiting_options():
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

    def _answered_in_jenkins(self, build: int) -> bool:
        try:
            return not self._jenkins.pending_inputs(build)
        except ServiceUnavailable:
            return False  # 조회 실패는 답이 아니다. 계속 기다린다

    def _wait_for_answer(self, request: Mapping[str, Any]) -> str:
        """사람의 답을 기다린다. Jenkins 화면에서 직접 답했으면 그 값으로 끝낸다. 답이 없으면 진행하지 않는다."""
        node_id, build = request["node_id"], request.get("jenkins_build")
        self._answered.clear()
        self._board.update(node_id, state="waiting", decision={
            "prompt": request["prompt"], "options": [{"value": value, "label": label} for value, label in request["options"]]})
        deadline = time.time() + DECISION_DEADLINE_SECONDS
        try:
            while time.time() < deadline:
                if self._answered.wait(timeout=DECISION_POLL_SECONDS):
                    return self._answer or "stop"
                if build is not None and self._answered_in_jenkins(build):
                    return ELSEWHERE
            raise TimeoutError("사람 확인 시간이 지났다")
        finally:
            self._board.update(node_id, decision=None)
