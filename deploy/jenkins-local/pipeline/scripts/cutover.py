#!/usr/bin/env python3
"""서비스별 단계 전환. 비율을 바꿀 때마다 그 서비스만 지표를 재고 deployment_gate 판단을 받는다.

  auto     파이프라인이 --steps 순서로 옮긴다. 화면에서 되돌리면 멈춘다
  manual   사람이 traffic-control 화면에서 비율을 바꾼다. 파이프라인은 바뀐 서비스만 재고 판단한다
  rollback 완료하지 않은 서비스를 출발 환경 100% 로 되돌린다. traffic-control 없이 HAProxy 에 바로 한다

종료 코드: 0 전부 이전, 2 일부만 이전(UNSTABLE), 1 되돌림·실패, 30 판단 서비스·화면에 닿지 못함
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import gate
import haproxy_ctl
import traffic

EXIT_DONE = 0
EXIT_FAILED = 1
EXIT_PARTIAL = 2
EXIT_UNAVAILABLE = 30
CONTROL_TIMEOUT_SECONDS = 10
POLL_SECONDS = 1
ACK_TIMEOUT_SECONDS = 600
MAX_PERCENT = 100


class ControlUnavailable(RuntimeError):
    """traffic-control 에 닿지 못했다."""


class ControlRejected(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class Control:
    def __init__(self, url: str, token: str) -> None:
        self._url, self._token = url.rstrip("/"), token

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self._url + path, data=data, method=method,
                                         headers={"Authorization": f"Bearer {self._token}",
                                                  "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=CONTROL_TIMEOUT_SECONDS) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                message = json.load(exc).get("error", "")
            except (json.JSONDecodeError, ValueError):
                message = ""
            raise ControlRejected(exc.code, message) from exc
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise ControlUnavailable(type(exc).__name__) from exc

    def state(self) -> dict[str, Any]:
        return self._call("GET", "/api/state")

    def events(self, after: int) -> list[dict[str, Any]]:
        return self._call("GET", f"/api/events?after={after}")["events"]

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", path, body)


@dataclass(frozen=True)
class Settings:
    run_id: str
    source: str
    target: str
    version: str
    gateway: str
    requests: int
    max_p95_ms: float
    hold_seconds: float
    out_dir: Path


@dataclass(frozen=True)
class Outcome:
    state: str  # passed | needs_ack | rolled_back
    detail: str


def load_services(path: Path) -> dict[str, dict[str, Any]]:
    config = json.loads(path.read_text(encoding="utf-8"))
    return {s["id"]: s for s in config["services"]}


def parse_steps(text: str, services: dict[str, Any]) -> list[tuple[str, list[int]]]:
    """'web=10,50,100;api=25,100'. 비율은 올라가기만 하고 100 으로 끝나야 한다."""
    plan = []
    for part in (p.strip() for p in text.split(";") if p.strip()):
        name, _, values = part.partition("=")
        name = name.strip()
        if name not in services:
            raise ValueError(f"모르는 서비스: {name}")
        steps = [int(v) for v in values.split(",") if v.strip()]
        if not steps or steps[-1] != MAX_PERCENT or any(b <= a for a, b in itertools.pairwise(steps)) or steps[0] <= 0:
            raise ValueError(f"{name} 단계는 0 초과로 올라가며 100 으로 끝나야 한다: {values}")
        plan.append((name, steps))
    if len({name for name, _ in plan}) != len(plan):
        raise ValueError("같은 서비스를 두 번 적었다")
    return plan


def sample(url: str, count: int) -> list[traffic.Sample]:
    with ThreadPoolExecutor(max_workers=traffic.WORKERS) as pool:
        return list(pool.map(traffic.fetch, [url] * count))


def measure(service: dict[str, Any], percent: int, settings: Settings, *,
            sampler: Callable[[str, int], list[traffic.Sample]] = sample,
            judge: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> Outcome:
    """기준 초과는 판단을 기다리지 않고 되돌린다. 판단을 못 받으면 통과로 보지 않는다."""
    sid = service["id"]
    summary = traffic.summarize(sampler(settings.gateway + service["path"], settings.requests), settings.version,
                                max_error_rate=0.05, max_p95_ms=settings.max_p95_ms)
    (settings.out_dir / f"metrics-{sid}-{percent}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    rate = summary["metrics"][0]["value"]
    error_rate = "없음" if rate is None else f"{rate:.3f}"
    if traffic.exceeded(summary):
        return Outcome("rolled_back", f"지표 기준 초과(오류율 {error_rate})")
    stage = f"cutover-{sid}-{percent}"
    body = gate.deployment_request(assessment_id=f"{settings.run_id}-{stage}", service_id=sid,
                                   commit=os.environ.get("RELEASE_ID", settings.run_id), stage=stage,
                                   status="succeeded", target=settings.target, traffic_percent=percent,
                                   metrics=tuple(summary["metrics"]))
    try:
        result = (judge or _judge)(body)
    except gate.GateUnavailable as exc:
        gate.append_event(settings.out_dir / "events.jsonl", run_id=settings.run_id, step=stage, state="unavailable",
                          verdict=None)
        return Outcome("rolled_back", f"판단을 받지 못했다({exc})")
    (settings.out_dir / f"gate-{sid}-{percent}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    gate.append_event(settings.out_dir / "events.jsonl", run_id=settings.run_id, step=stage, state="judged",
                      verdict=result.get("verdict"))
    verdict = result.get("verdict")
    if verdict == "pass":
        return Outcome("passed", f"판단 통과(오류율 {error_rate})")
    if verdict == "needs_human":
        return Outcome("needs_ack", f"사람 확인 필요: {','.join(result.get('reasons', []))}")
    return Outcome("rolled_back", f"판단 {verdict}")


def _judge(body: dict[str, Any]) -> dict[str, Any]:
    return gate.call_gate(os.environ["DELTA_URL"], os.environ["DELTA_API_TOKEN"], body)


def save_states(control: Control, path: Path) -> dict[str, str]:
    states = {s["id"]: s["state"] for s in control.state()["services"]}
    path.write_text(json.dumps({"services": states}, ensure_ascii=False, indent=2))
    return states


def report(control: Control, service_id: str, outcome: Outcome, percent: int) -> str:
    """판단 결과를 화면에 알린다. 그 사이 사람이 되돌렸으면(409) 되돌린 것으로 본다."""
    try:
        event = control.post(f"/api/services/{service_id}/report",
                             {"state": outcome.state, "detail": outcome.detail, "percent": percent})
        return event.get("state", outcome.state) if event.get("kind") == "judged" else "rolled_back"
    except ControlRejected as exc:
        if exc.status == 409:
            return "rolled_back"
        raise
    finally:
        print(f"{service_id} {percent}%: {outcome.state} — {outcome.detail}", flush=True)


def wait_for_human(control: Control, service_id: str, after: int, timeout: float = ACK_TIMEOUT_SECONDS) -> bool:
    """needs_ack 에서 화면의 확인(True)이나 되돌림(False)을 기다린다."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for event in control.events(after):
            after = event["seq"]
            if event.get("service") == service_id and event["kind"] in ("ack", "rollback"):
                return event["kind"] == "ack"
        time.sleep(POLL_SECONDS)
    return False


def _latest_seq(control: Control) -> int:
    return int(control.state()["seq"])


def run_auto(control: Control, services: dict[str, Any], steps: list[tuple[str, list[int]]], settings: Settings,
             state_file: Path, *, measurer: Callable[..., Outcome] = measure,
             sleep: Callable[[float], None] = time.sleep) -> int:
    control.post("/api/run", {"run_id": settings.run_id, "mode": "auto", "source": settings.source,
                              "target": settings.target})
    failed = False
    try:
        for service_id, percents in steps:
            for percent in percents:
                seq = _latest_seq(control)
                try:
                    control.post(f"/api/services/{service_id}/weight", {"target_percent": percent})
                except ControlRejected as exc:
                    print(f"{service_id} {percent}% 로 바꾸지 못했다: {exc}", file=sys.stderr)
                    failed = True
                    break
                sleep(settings.hold_seconds)  # 화면에서 비율이 보이게 잠시 둔다
                outcome = measurer(services[service_id], percent, settings)
                final = report(control, service_id, outcome, percent)
                if final == "needs_ack":
                    if not wait_for_human(control, service_id, seq):
                        failed = True
                        break
                elif final == "rolled_back":
                    failed = True
                    break
            save_states(control, state_file)
            if failed:
                break
    finally:
        states = save_states(control, state_file)
        result = "failed" if failed else ("completed" if all(s == "completed" for s in states.values()) else "partial")
        control.post("/api/run/end", {"run_id": settings.run_id, "result": result})
    return EXIT_FAILED if failed else EXIT_DONE


def run_manual(control: Control, services: dict[str, Any], settings: Settings, state_file: Path, timeout: float, *,
               measurer: Callable[..., Outcome] = measure, sleep: Callable[[float], None] = time.sleep,
               clock: Callable[[], float] = time.monotonic) -> int:
    control.post("/api/run", {"run_id": settings.run_id, "mode": "manual", "source": settings.source,
                              "target": settings.target})
    seq = _latest_seq(control)
    deadline = clock() + timeout
    finished = False
    print(f"수동 전환 대기: 화면에서 서비스별 비율을 바꾼다. 최대 {int(timeout)}초", flush=True)
    try:
        while not finished and clock() < deadline:
            for event in control.events(seq):
                seq = event["seq"]
                if event["kind"] == "finish":
                    finished = True
                elif event["kind"] == "weight_set" and event["actor"] == "ui" and event["to"] > 0:
                    outcome = measurer(services[event["service"]], event["to"], settings)
                    report(control, event["service"], outcome, event["to"])
                elif event["kind"] == "rollback":
                    print(f"{event['service']}: {event['actor']} 가 되돌렸다", flush=True)
            save_states(control, state_file)
            if not finished:
                sleep(POLL_SECONDS)
    finally:
        states = save_states(control, state_file)
        done = all(s == "completed" for s in states.values())
        result = "completed" if finished and done else "partial" if finished else "timeout"
        control.post("/api/run/end", {"run_id": settings.run_id, "result": result})
    print(f"수동 전환 끝: {json.dumps(states, ensure_ascii=False)}")
    if not finished:
        return EXIT_FAILED
    return EXIT_DONE if done else EXIT_PARTIAL


def rollback_unfinished(services: dict[str, Any], state_file: Path, source: str, target: str, *,
                        apply: Callable[..., None] = haproxy_ctl.apply_weights) -> list[str]:
    """상태 파일이 없으면 전부 되돌린다. 출발 환경은 전환 내내 살아 있으므로 안전한 쪽이다."""
    states = json.loads(state_file.read_text())["services"] if state_file.is_file() else {}
    rolled = []
    for service_id, service in services.items():
        if states.get(service_id) == "completed":
            continue
        apply(service["backend"], {source: MAX_PERCENT, target: 0})
        rolled.append(service_id)
    return rolled


def _settings(args: argparse.Namespace) -> Settings:
    return Settings(run_id=args.run_id, source=args.source, target=args.target, version=args.version,
                    gateway=os.environ.get("GATEWAY_URL", "http://haproxy:81"), requests=args.requests,
                    max_p95_ms=args.max_p95_ms, hold_seconds=args.hold, out_dir=Path.cwd())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("auto", "manual", "rollback", "end"))
    parser.add_argument("--services", type=Path, required=True)
    parser.add_argument("--state", type=Path, default=Path("cutover-state.json"))
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--run-id", default="local")
    parser.add_argument("--version", default="")
    parser.add_argument("--steps", default="")
    parser.add_argument("--requests", type=int, default=400)
    parser.add_argument("--max-p95-ms", type=float, default=2000)
    parser.add_argument("--hold", type=float, default=5)
    parser.add_argument("--timeout", type=float, default=1500)
    args = parser.parse_args(argv)
    services = load_services(args.services)

    if args.mode == "rollback":
        rolled = rollback_unfinished(services, args.state, args.source, args.target)
        print(f"출발 환경으로 되돌림: {', '.join(rolled) or '없음'}")
        return EXIT_DONE
    control = Control(os.environ.get("TRAFFIC_CONTROL_URL", "http://traffic-control:8080"),
                      os.environ["TRAFFIC_CONTROL_TOKEN"])
    if args.mode == "end":  # 중단된 빌드 뒷정리. 이미 끝났으면 할 일이 없다
        try:
            control.post("/api/run/end", {"run_id": args.run_id, "result": "aborted"})
        except (ControlUnavailable, ControlRejected) as exc:
            print(f"전환 종료 알림 생략: {exc}")
        return EXIT_DONE
    # Jenkins 중단은 SIGTERM 이다. finally 의 전환 종료 알림이 돌도록 예외로 바꾼다
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(EXIT_FAILED))
    try:
        if args.mode == "auto":
            return run_auto(control, services, parse_steps(args.steps, services), _settings(args), args.state)
        return run_manual(control, services, _settings(args), args.state, args.timeout)
    except ValueError as exc:
        print(f"단계 설정 오류: {exc}", file=sys.stderr)
        return EXIT_FAILED
    except (ControlUnavailable, ControlRejected) as exc:
        print(f"traffic-control 호출 실패: {exc}", file=sys.stderr)
        return EXIT_UNAVAILABLE


if __name__ == "__main__":
    sys.exit(main())
