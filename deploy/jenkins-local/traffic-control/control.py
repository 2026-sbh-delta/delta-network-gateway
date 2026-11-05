"""서비스별 이전 비율 상태. HTTP 와 HAProxy 를 모른다(주입받는다).

실제 가중치는 매번 HAProxy 에서 읽고, 여기에는 진행 중인 전환과 서비스 상태만 둔다.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

MAX_PERCENT = 100
EVENT_LIMIT = 500
RECENT_EVENTS = 40
ACTORS = ("ui", "pipeline")
MODES = ("auto", "manual")
# 이 상태에서는 비율을 바꾸지 못한다. 롤백은 언제나 된다
LOCKED = {"measuring": "측정 중이다", "rolled_back": "이번 실행에서 되돌린 서비스다",
          "needs_ack": "사람 확인이 먼저다"}
ANY_RUN = "*"  # 파이프라인이 앞 빌드가 남긴 전환을 정리할 때 쓴다
REPORTABLE = ("passed", "needs_ack", "rolled_back", "source")


class ControlError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class Haproxy(Protocol):
    def weights(self, backend: str) -> dict[str, int]: ...
    def set_weights(self, backend: str, weights: Mapping[str, int]) -> None: ...
    def statuses(self) -> dict[tuple[str, str], str]: ...


@dataclass
class Run:
    run_id: str
    mode: str
    source: str
    target: str
    started_at: float
    active: bool = True
    finish_requested: bool = False
    result: str | None = None


@dataclass
class ServiceState:
    state: str = "idle"
    detail: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)


class Controller:
    def __init__(self, services: Sequence[Mapping[str, Any]], servers: Sequence[str], haproxy: Haproxy,
                 clock: Callable[[], float] = time.time) -> None:
        self._lock = threading.RLock()
        self._services = {s["id"]: dict(s) for s in services}
        self._servers = tuple(servers)
        self._haproxy = haproxy
        self._clock = clock
        self._run: Run | None = None
        self._states = {service_id: ServiceState() for service_id in self._services}
        self._events: list[dict[str, Any]] = []
        self._seq = 0

    # ---- 읽기 ----

    def _service(self, service_id: str) -> dict[str, Any]:
        if service_id not in self._services:
            raise ControlError(404, f"모르는 서비스: {service_id}")
        return self._services[service_id]

    def _target_percent(self, service_id: str, weights: Mapping[str, int] | None = None) -> int | None:
        if self._run is None:
            return None
        weights = weights or self._haproxy.weights(self._service(service_id)["backend"])
        return weights[self._run.target]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            statuses = self._haproxy.statuses()
            services = []
            for service_id, service in self._services.items():
                weights = self._haproxy.weights(service["backend"])
                state = self._states[service_id]
                active = self._run is not None and self._run.active
                # 끝난 실행의 상태는 그 실행 것이다. 뒤에 가중치가 바뀌었을 수 있으니 대기로 보이고 기록만 남긴다
                shown, detail = (state.state, state.detail) if active else ("idle", f"지난 실행: {state.state}"
                                                                         if state.state != "idle" else "")
                services.append({"id": service_id, "path": service["path"], "backend": service["backend"],
                                 "weights": weights,
                                 "status": {s: statuses.get((service["backend"], s), "UNKNOWN") for s in self._servers},
                                 "target_percent": self._target_percent(service_id, weights),
                                 "state": shown, "detail": detail})
            run = vars(self._run).copy() if self._run else None
            return {"run": run, "services": services, "events": self._events[-RECENT_EVENTS:], "seq": self._seq}

    def events_after(self, seq: int) -> list[dict[str, Any]]:
        with self._lock:
            return [e for e in self._events if e["seq"] > seq]

    # ---- 쓰기 ----

    def _event(self, actor: str, kind: str, service: str | None = None, **detail: Any) -> dict[str, Any]:
        self._seq += 1
        event = {"seq": self._seq, "at": self._clock(), "actor": actor, "kind": kind, "service": service, **detail}
        self._events = [*self._events[-(EVENT_LIMIT - 1):], event]
        return event

    def _active_run(self) -> Run:
        if self._run is None or not self._run.active:
            raise ControlError(409, "진행 중인 전환이 없다")
        return self._run

    def _set_state(self, service_id: str, state: str, detail: str = "") -> None:
        current = self._states[service_id]
        current.state, current.detail = state, detail

    def start_run(self, run_id: str, mode: str, source: str, target: str) -> None:
        if mode not in MODES or {source, target} - set(self._servers) or source == target:
            raise ControlError(400, "모드나 방향이 잘못됐다")
        with self._lock:
            if self._run and self._run.active and self._run.run_id != run_id:
                raise ControlError(409, f"다른 전환이 진행 중이다: {self._run.run_id}")
            percents = {sid: self._haproxy.weights(s["backend"])[target] for sid, s in self._services.items()}
            stuck = [sid for sid, p in percents.items() if 0 < p < MAX_PERCENT]
            if stuck:
                raise ControlError(409, f"중간 비율에 멈춘 서비스가 있다: {', '.join(stuck)}. 먼저 출발 환경으로 되돌린다")
            self._run = Run(run_id, mode, source, target, self._clock())
            for sid, percent in percents.items():
                self._set_state(sid, "completed" if percent == MAX_PERCENT else "source")
            self._event("pipeline", "run_started", mode=mode, source=source, target=target, run_id=run_id)

    def end_run(self, run_id: str, result: str) -> None:
        with self._lock:
            run = self._active_run()
            if run_id not in (run.run_id, ANY_RUN):
                raise ControlError(409, "다른 전환이다")
            run.active, run.result = False, result
            self._event("pipeline", "run_ended", result=result, run_id=run_id)

    def set_weight(self, service_id: str, percent: Any, actor: str) -> dict[str, Any]:
        if not isinstance(percent, int) or isinstance(percent, bool) or not 0 <= percent <= MAX_PERCENT:
            raise ControlError(400, "비율은 0~100 정수다")
        with self._lock:
            run = self._active_run()
            expected = "manual" if actor == "ui" else "auto"
            if run.mode != expected:
                who = "화면" if actor == "ui" else "파이프라인"
                raise ControlError(403, f"{run.mode} 모드에서는 {who}이 비율을 바꾸지 않는다")
            service = self._service(service_id)
            state = self._states[service_id]
            if state.state in LOCKED:
                raise ControlError(409, f"{service_id}: {LOCKED[state.state]}")
            current = self._target_percent(service_id)
            if percent == current:
                raise ControlError(400, f"{service_id} 는 이미 {percent}% 다")
            if percent > 0 and self._haproxy.statuses().get((service["backend"], run.target)) != "UP":
                raise ControlError(409, f"{service_id} 의 {run.target} 입구가 UP 이 아니다")
            self._haproxy.set_weights(service["backend"], {run.target: percent, run.source: MAX_PERCENT - percent})
            self._set_state(service_id, "measuring" if percent > 0 else "source", f"{current}% → {percent}%")
            return self._event(actor, "weight_set", service_id, **{"from": current, "to": percent})

    def rollback(self, service_id: str, actor: str, reason: str = "") -> dict[str, Any]:
        with self._lock:
            run = self._active_run()
            service = self._service(service_id)
            current = self._target_percent(service_id)
            self._haproxy.set_weights(service["backend"], {run.source: MAX_PERCENT, run.target: 0})
            self._set_state(service_id, "rolled_back", reason or f"{actor} 가 되돌림")
            return self._event(actor, "rollback", service_id, **{"from": current, "to": 0, "reason": reason})

    def ack(self, service_id: str) -> dict[str, Any]:
        with self._lock:
            self._active_run()
            self._service(service_id)
            if self._states[service_id].state != "needs_ack":
                raise ControlError(409, f"{service_id} 는 확인할 것이 없다")
            final = "completed" if self._target_percent(service_id) == MAX_PERCENT else "passed"
            self._set_state(service_id, final, "사람이 확인함")
            return self._event("ui", "ack", service_id)

    def report(self, service_id: str, state: str, detail: str, percent: int | None) -> dict[str, Any]:
        """파이프라인이 측정·판단 결과를 알린다. 100% 에서 통과하면 completed 로 본다."""
        if state not in REPORTABLE:
            raise ControlError(400, f"알릴 수 없는 상태: {state}")
        with self._lock:
            self._active_run()
            self._service(service_id)
            if state == "rolled_back":
                return self.rollback(service_id, "pipeline", detail)
            if self._states[service_id].state == "rolled_back":
                # 측정하는 사이 사람이 되돌렸다. 늦게 온 통과로 덮지 않는다
                raise ControlError(409, f"{service_id} 는 이미 되돌렸다")
            final = "completed" if state == "passed" and percent == MAX_PERCENT else state
            self._set_state(service_id, final, detail)
            return self._event("pipeline", "judged", service_id, state=final, percent=percent, detail=detail)

    def finish(self) -> dict[str, Any]:
        with self._lock:
            run = self._active_run()
            if run.mode != "manual":
                raise ControlError(403, "자동 모드는 파이프라인이 끝낸다")
            measuring = [sid for sid, s in self._states.items() if s.state == "measuring"]
            if measuring:
                raise ControlError(409, f"측정 중인 서비스가 있다: {', '.join(measuring)}")
            stuck = [sid for sid in self._services if 0 < (self._target_percent(sid) or 0) < MAX_PERCENT]
            if stuck:
                raise ControlError(409, f"중간 비율인 서비스가 있다: {', '.join(stuck)}. 0% 나 100% 로 정한다")
            run.finish_requested = True
            return self._event("ui", "finish")
