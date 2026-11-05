"""실행 입력과 화면에 보여 줄 노드 상태."""

from __future__ import annotations

import json
import threading
import time
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO_PREFIX = "https://github.com/2026-sbh-delta/"


@dataclass(frozen=True)
class Options:
    """한 실행의 입력. 커밋은 40자리 SHA로 고정해 받는다(브랜치 이름은 받지 않는다)."""
    repo: str
    commit: str
    planner: str = "agent"


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
    """웹 갈래(명세 v1.1 6-2)의 최소 연결 노드. 판정은 그래프 분기(코드)가 한다."""
    return [
        Node("prepare", "입력 확인", "오케스트레이터"),
        Node("build", "빌드", "Jenkins · 빌드 에이전트"),
        Node("sast", "SAST", "delta-sast"),
        Node("stage", "스테이징 배포", "Jenkins · 스테이징 도구"),
        Node("dast", "DAST", "delta-dast · Chrome"),
        Node("result", "결과", "오케스트레이터"),
    ]


class Board:
    """화면이 읽는 노드 상태. 재시작해도 이어 보이도록 바뀔 때마다 파일에 남긴다.

    판단과 전이의 기준은 그래프 체크포인트다. 이 파일은 표시용이다.
    """

    def __init__(self, path: Path, options: Options, run_id: str) -> None:
        self._path, self._options, self._run_id = path, options, run_id
        self._lock = threading.Lock()
        self._nodes = {node.id: node for node in initial_nodes()}
        self._outcome: str | None = None
        self._started_at = time.time()
        if path.is_file():
            saved = json.loads(path.read_text(encoding="utf-8"))
            self._nodes = {item["id"]: Node(**item) for item in saved["nodes"]}
            self._outcome, self._started_at = saved["outcome"], saved["started_at"]
        else:
            self._save()

    def _payload(self) -> dict[str, Any]:
        return {"run_id": self._run_id, "options": asdict(self._options), "outcome": self._outcome, "started_at": self._started_at,
                "nodes": [deepcopy(asdict(node)) for node in self._nodes.values()]}

    def _save(self) -> None:
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._payload(), ensure_ascii=False), encoding="utf-8")
        temporary.replace(self._path)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._payload()

    def update(self, node_id: str, **fields: Any) -> None:
        with self._lock:
            node = self._nodes[node_id]
            for key, value in fields.items():
                setattr(node, key, value)
            if fields.get("state") == "running" and node.started_at is None:
                node.started_at = time.time()
            if fields.get("state") in ("pass", "fail", "skipped"):
                node.ended_at = time.time()
            self._save()

    def add_detail(self, node_id: str, **values: Any) -> None:
        with self._lock:
            self._nodes[node_id].detail = {**self._nodes[node_id].detail, **values}
            self._save()

    def state_of(self, node_id: str) -> str:
        with self._lock:
            return self._nodes[node_id].state

    def waiting_options(self) -> set[str]:
        with self._lock:
            waiting = next((n for n in self._nodes.values() if n.decision), None)
            return {o["value"] for o in waiting.decision["options"]} if waiting else set()

    def finish(self, outcome: str, summary: str, *, passed: bool = False) -> None:
        with self._lock:
            for node in self._nodes.values():
                if node.state == "pending" and node.id != "result":
                    node.state = "skipped"
                elif node.state in ("running", "waiting"):
                    node.state, node.ended_at, node.decision = "fail", time.time(), None
            self._outcome = outcome
            self._save()
        self.update("result", state="pass" if passed else "fail", summary=summary)
