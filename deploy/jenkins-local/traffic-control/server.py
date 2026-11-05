#!/usr/bin/env python3
"""서비스별 이전 비율 화면과 API. 로컬 시험 전용이다.

- 파이프라인: Authorization: Bearer <TRAFFIC_CONTROL_TOKEN>
- 화면: /api/config 가 주는 CSRF 토큰을 X-Control-Token 으로 보낸다. 다른 사이트 페이지는 이 값을 읽지 못한다
HAProxy 관리 포트는 compose 네트워크 안에만 있고, 이 서버만 밖(127.0.0.1)에 연다.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, deque
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import haproxy_ctl
from control import ControlError, Controller

HERE = Path(__file__).resolve().parent
MAX_BODY_BYTES = 4096
PROBE_INTERVAL_SECONDS = 0.3
PROBE_WINDOW_SECONDS = 10
PROBE_TIMEOUT_SECONDS = 2
SERVICE_ROUTE = re.compile(r"^/api/services/([a-z0-9-]+)/(weight|rollback|ack|report)$")


class LiveHaproxy:
    def __init__(self, servers: tuple[str, ...]) -> None:
        self._servers = servers

    def weights(self, backend: str) -> dict[str, int]:
        return haproxy_ctl.read_weights(backend, self._servers)

    def set_weights(self, backend: str, weights: Mapping[str, int]) -> None:
        haproxy_ctl.apply_weights(backend, weights)

    def statuses(self) -> dict[tuple[str, str], str]:
        result = {}
        lines = [line for line in haproxy_ctl.run_command("show stat").splitlines() if line.strip()]
        header = lines[0].lstrip("# ").split(",") if lines else []
        for line in lines[1:]:
            row = dict(zip(header, line.split(",")))
            result[(row.get("pxname", ""), row.get("svname", ""))] = row.get("status", "")
        return result


class LiveTraffic(threading.Thread):
    """게이트웨이에 서비스마다 조금씩 요청을 보내 실제로 어느 클라우드가 응답하는지 센다. 설정 비율과 비교용이다."""

    def __init__(self, gateway: str, services: list[dict[str, Any]]) -> None:
        super().__init__(daemon=True)
        self._gateway, self._services = gateway, services
        self._lock = threading.Lock()
        self._samples: dict[str, deque[tuple[float, str, bool]]] = {s["id"]: deque() for s in services}

    @staticmethod
    def _probe(url: str) -> tuple[str, bool]:
        try:
            with urllib.request.urlopen(url, timeout=PROBE_TIMEOUT_SECONDS) as response:
                return (response.headers.get("X-App-Version") or "?").split("-", 1)[0], False
        except urllib.error.HTTPError as exc:
            return (exc.headers.get("X-App-Version") or "?").split("-", 1)[0], exc.code >= 500
        except (urllib.error.URLError, TimeoutError, OSError):
            return "응답 없음", True

    def run(self) -> None:
        while True:
            for service in self._services:
                cloud, failed = self._probe(self._gateway + service["path"])
                now = time.time()
                with self._lock:
                    window = self._samples[service["id"]]
                    window.append((now, cloud, failed))
                    while window and window[0][0] < now - PROBE_WINDOW_SECONDS:
                        window.popleft()
            time.sleep(PROBE_INTERVAL_SECONDS)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {sid: {"clouds": dict(Counter(c for _, c, _ in w)), "failed": sum(f for *_, f in w), "total": len(w)}
                    for sid, w in self._samples.items()}


class Handler(BaseHTTPRequestHandler):
    controller: Controller
    live: LiveTraffic | None
    pipeline_token: str
    csrf_token: str
    allowed_hosts: frozenset[str]

    def log_message(self, *_: Any) -> None:
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                                                    "script-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, value: Any, status: int = 200) -> None:
        self._send(status, json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _host_ok(self) -> bool:
        """DNS 리바인딩으로 다른 이름이 이 서버를 가리켜도 쓰기를 받지 않는다."""
        return self.headers.get("Host", "") in self.allowed_hosts

    def _actor(self) -> str | None:
        bearer = self.headers.get("Authorization", "")
        if bearer.startswith("Bearer ") and hmac.compare_digest(bearer[7:], self.pipeline_token):
            return "pipeline"
        csrf = self.headers.get("X-Control-Token", "")
        if csrf and hmac.compare_digest(csrf, self.csrf_token):
            return "ui"
        return None

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            raise ControlError(413, "본문이 너무 크다")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ControlError(400, "JSON 이 아니다") from exc
        if not isinstance(body, dict):
            raise ControlError(400, "JSON 객체여야 한다")
        return body

    def do_GET(self) -> None:
        if self.path == "/healthz":  # 컨테이너 상태 확인은 127.0.0.1:8080 으로 온다. 상태 값을 내지 않는다
            self._json({"ok": True})
            return
        if not self._host_ok():
            self._json({"error": "허용하지 않는 Host"}, 403)
            return
        path, _, query = self.path.partition("?")
        try:
            if path == "/":
                self._send(200, (HERE / "static" / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/config":
                self._json({"csrf_token": self.csrf_token})
            elif path == "/api/state":
                state = self.controller.snapshot()
                state["live"] = self.live.snapshot() if self.live else {}
                self._json(state)
            elif path == "/api/events":
                after = int(dict(p.split("=", 1) for p in query.split("&") if "=" in p).get("after", "0"))
                self._json({"events": self.controller.events_after(after)})
            else:
                self._json({"error": "없는 경로"}, 404)
        except ValueError:
            self._json({"error": "after 는 정수다"}, 400)
        except (OSError, RuntimeError) as exc:
            self._json({"error": f"HAProxy 에 닿지 못했다: {type(exc).__name__}"}, 503)

    def do_POST(self) -> None:
        if not self._host_ok():
            self._json({"error": "허용하지 않는 Host"}, 403)
            return
        actor = self._actor()
        if actor is None:
            self._json({"error": "인증이 없다"}, 401)
            return
        try:
            self._json(self._dispatch(actor, self.path, self._body()))
        except ControlError as exc:
            self._json({"error": exc.message}, exc.status)
        except (OSError, RuntimeError, ValueError) as exc:
            self._json({"error": f"HAProxy 적용 실패: {exc}"}, 503)

    def _dispatch(self, actor: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        c = self.controller
        if path == "/api/finish" and actor == "ui":
            return c.finish()
        if path in ("/api/run", "/api/run/end"):
            if actor != "pipeline":
                raise ControlError(403, "전환 시작·종료는 파이프라인만 한다")
            if path == "/api/run":
                c.start_run(str(body.get("run_id", "")), body.get("mode"), body.get("source"), body.get("target"))
            else:
                c.end_run(str(body.get("run_id", "")), str(body.get("result", "")))
            return {"ok": True}
        match = SERVICE_ROUTE.match(path)
        if not match:
            raise ControlError(404, "없는 경로")
        service_id, action = match.groups()
        if action == "weight":
            return c.set_weight(service_id, body.get("target_percent"), actor)
        if action == "rollback":
            return c.rollback(service_id, actor, str(body.get("reason", ""))[:200])
        if action == "ack" and actor == "ui":
            return c.ack(service_id)
        if action == "report" and actor == "pipeline":
            return c.report(service_id, str(body.get("state")), str(body.get("detail", ""))[:500], body.get("percent"))
        raise ControlError(403, f"{actor} 는 {action} 을 할 수 없다")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--services", type=Path, default=Path(os.environ.get("TRAFFIC_SERVICES", "/config/services.json")))
    parser.add_argument("--gateway", default=os.environ.get("GATEWAY_URL", "http://haproxy:81"))
    parser.add_argument("--public-port", default=os.environ.get("PUBLIC_PORT", "18082"))
    args = parser.parse_args(argv)
    token = os.environ.get("TRAFFIC_CONTROL_TOKEN", "")
    if len(token) < 32:
        print("TRAFFIC_CONTROL_TOKEN 이 없거나 짧다(32자 이상)", file=sys.stderr)
        return 1
    config = json.loads(args.services.read_text(encoding="utf-8"))
    Handler.controller = Controller(config["services"], config["servers"], LiveHaproxy(tuple(config["servers"])))
    Handler.live = LiveTraffic(args.gateway, config["services"])
    Handler.live.start()
    Handler.pipeline_token = token
    Handler.csrf_token = secrets.token_urlsafe(32)  # 재시작마다 바뀐다. 화면은 다시 읽는다
    Handler.allowed_hosts = frozenset({f"127.0.0.1:{args.public_port}", f"localhost:{args.public_port}",
                                       f"traffic-control:{args.port}"})
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
