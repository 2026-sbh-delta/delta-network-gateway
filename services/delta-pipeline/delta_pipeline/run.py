"""실행 한 번을 구동한다. 사람 확인을 기다리고, 강제 종료 뒤에는 체크포인트에서 이어 간다."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import fields, replace
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from delta_pipeline.clients import ServiceUnavailable
from delta_pipeline.diagnosis import diagnose
from delta_pipeline.graph import build_graph
from delta_pipeline.model import Board, Options
from delta_pipeline.steps import CloudflareClient, CutoverConfig, DastClient, Deps, JenkinsClient, SastClient, StagingClient

# Human reviews may span a meeting; no answer still stops the run.
DECISION_DEADLINE_SECONDS = 3 * 60 * 60
DECISION_POLL_SECONDS = 0.5
CHECKPOINT_FILE = "checkpoints.sqlite"
BOARD_DIR = "runs"
LOG = logging.getLogger(__name__)


def unfinished_runs(store_dir: Path) -> list[tuple[str, Options]]:
    """강제 종료로 끝나지 못한 가장 최근 실행을 찾는다."""
    boards = sorted((store_dir / BOARD_DIR).glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    unfinished = []
    for path in boards:
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved["outcome"] is None and "options" in saved:
            # 다른 버전 오케스트레이터가 같은 상태 폴더에 남긴 실행(모르는 옵션)은 이어 받지 않는다.
            # 이어 받으면 그 옵션을 버린 채 엉뚱한 환경으로 실행하거나, 시작 자체가 실패한다(11/6 실측).
            if set(saved["options"]) - {f.name for f in fields(Options)}:
                LOG.warning("실행 %s: 이 버전이 모르는 옵션이 있어 이어 받지 않는다", saved.get("run_id"))
                continue
            unfinished.append((saved["run_id"], Options(**saved["options"])))
    return unfinished


def latest_unfinished(store_dir: Path) -> tuple[str, Options] | None:
    return next(iter(unfinished_runs(store_dir)), None)


class Run:
    def __init__(self, options: Options, *, sast: SastClient, dast: DastClient, build_jenkins: JenkinsClient,
                 deploy_jenkins: JenkinsClient, staging: StagingClient, image_repo: str, store_dir: Path,
                 sleep: Callable[[float], None] = time.sleep, run_id: str | None = None,
                 cloudflare: CloudflareClient | None = None, cutover: CutoverConfig | None = None,
                 probe: Callable[..., tuple[int, str]] | None = None,
                 resolve: Callable[[str], str | None] | None = None, now: Callable[[], float] | None = None,
                 release_jenkins: JenkinsClient | None = None, release_signing_key: bytes | None = None,
                 release_config: dict[str, Any] | None = None, ecr_image_repo: str = "",
                 diagnostic: Any = None, report_dir: Path | None = None, evidence: Any = None, onprem: Any = None,
                 diagnose: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> None:
        self.run_id = run_id or uuid.uuid4().hex
        if diagnostic is not None:
            options = replace(options, diagnostics=True)
        self.options = options
        (store_dir / BOARD_DIR).mkdir(parents=True, exist_ok=True)
        self._board = Board(store_dir / BOARD_DIR / f"{self.run_id}.json", options, self.run_id)
        self._store_dir = store_dir
        if options.execution_cloud == 'onprem':
            self._board.update('stage',label='온프레미스 실행',owner='독립 온프레미스 실행기 · k3s')
            if onprem is not None and hasattr(onprem,'bind_board'):onprem.bind_board(self._board)
        self._cancelled = threading.Event()
        if any(n["id"] == "result" and n["detail"].get("cancel_requested") for n in self._board.snapshot()["nodes"]):
            self._cancelled.set()
        from delta_pipeline.notifications import Inbox
        self.inbox = Inbox(store_dir / "notifications") if options.assistance else None
        self._deps = Deps(run_id=self.run_id, options=options, board=self._board, sast=sast, dast=dast,
                          build_jenkins=build_jenkins, deploy_jenkins=deploy_jenkins, staging=staging,
                          image_repo=image_repo, sleep=sleep, cloudflare=cloudflare, cutover=cutover,
                          diagnostic=diagnostic, evidence=evidence, onprem=onprem,
                          cancelled=self._cancelled.is_set, inbox=self.inbox, report_dir=report_dir or store_dir / "reports",
                          release_jenkins=release_jenkins, release_signing_key=release_signing_key, release_config=release_config,
                          ecr_image_repo=ecr_image_repo,
                          **{k: v for k, v in (("probe", probe), ("resolve", resolve), ("now", now)) if v})
        self._waiting = False
        self._waiting_case = None
        self._answer_lock = threading.Lock()
        self._diagnose = diagnose if not options.diagnostics else None  # 멈춘 실행의 원인 진단(delta-ai). 없으면 하지 않는다
        self._answer: str | None = None
        self._answered = threading.Event()

    def cancel(self):
        if self.options.execution_cloud != "onprem":
            return False
        self._board.add_detail("result", cancel_requested=True)
        self._cancelled.set()
        return True

    def snapshot(self) -> dict[str, Any]:
        return self._board.snapshot()

    def answer(self, value: str, case_id: str | None = None, *, decision_id: str | None = None) -> bool:
        with self._answer_lock:
            if not self._waiting or self._answered.is_set() or value not in self._board.waiting_options():
                return False
            if self._waiting_case is not None and case_id != self._waiting_case:
                return False
            decisions = [n["decision"] for n in self.snapshot()["nodes"] if n.get("decision")]
            if decision_id is not None and not any(d.get("id") == decision_id for d in decisions):
                return False
            self._answer = value
            self._answered.set()
            return True

    def execute(self) -> None:
        connection = sqlite3.connect(self._store_dir / CHECKPOINT_FILE, check_same_thread=False)
        graph = None
        try:
            if self.options.diagnostics:
                from delta_pipeline.case_learning import CaseLearning
                try:
                    CaseLearning(self._deps).flush()
                except Exception:
                    LOG.warning("사례 재전송 미완료; 로컬 outbox를 보존한다")
            graph = build_graph(self._deps, SqliteSaver(connection))
            self._drive(graph)
        except TimeoutError as exc:
            self._board.finish("stopped", str(exc))
        except ServiceUnavailable as exc:
            self._board.finish("stopped", f"서비스 응답 없음, 통과로 보지 않는다: {exc}")
        except Exception:
            # 체크포인트는 남아 있다. 어느 노드에서 죽었는지는 로그로 본다
            LOG.exception("실행 %s 이 예상하지 못한 오류로 멈췄다", self.run_id)
            self._board.finish("stopped", "내부 오류로 멈췄다. 통과로 보지 않는다")
        finally:
            if graph is not None and self.options.diagnostics:
                try:
                    from delta_pipeline.case_learning import CaseLearning
                    CaseLearning(self._deps).unfinished(graph.get_state({"configurable": {"thread_id": self.run_id}}).values)
                except Exception:
                    LOG.warning("사례 최종 결과 확인 불가; 성공으로 처리하지 않는다")
            connection.close()
        if self._diagnose is not None:
            # 다음 실행을 막지 않게 따로 돈다. 결과(outcome)는 바꾸지 않는다
            threading.Thread(target=diagnose, args=(self._board, self.run_id, self.options, self._diagnose),
                             daemon=True).start()

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
            "id": uuid.uuid4().hex,
            "prompt": request["prompt"], "case_id": request.get("case_id"), "options": [{"value": value, "label": label} for value, label in request["options"]]})
        deadline = time.time() + DECISION_DEADLINE_SECONDS
        try:
            while time.time() < deadline:
                if self._cancelled.is_set():
                    return "stop"
                if self._answered.wait(timeout=DECISION_POLL_SECONDS):
                    return self._answer or "stop"
            raise TimeoutError("사람 확인 시간이 지났다")
        finally:
            self._waiting = False
            self._waiting_case = None
            self._board.update(node_id, decision=None)
