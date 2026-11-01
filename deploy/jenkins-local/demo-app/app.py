"""파이프라인 시험용 앱. FAIL_RATE 로 오류를 주입해 자동 롤백을 확인한다."""

from __future__ import annotations

import json
import os
import random
import signal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = os.environ.get("APP_VERSION", "dev")
FAIL_RATE = float(os.environ.get("FAIL_RATE", "0"))
PORT = 8080
LISTEN_BACKLOG = 128  # 기본값 5 는 동시 요청이 몰리면 SYN 재전송으로 1초씩 밀린다


def should_fail(rate: float, draw: float) -> bool:
    return draw < rate


class Server(ThreadingHTTPServer):
    request_queue_size = LISTEN_BACKLOG


class Handler(BaseHTTPRequestHandler):
    def _reply(self, status: int, body: dict[str, str]) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-App-Version", VERSION)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._reply(200, {"status": "ok"})
        elif self.path == "/api/hello":
            if should_fail(FAIL_RATE, random.random()):
                self._reply(500, {"error": "injected failure"})
            else:
                self._reply(200, {"message": "hello", "version": VERSION})
        else:
            self._reply(404, {"error": "not found"})

    def log_message(self, format: str, *args: object) -> None:
        return


def _stop(*_: object) -> None:
    raise KeyboardInterrupt


def main() -> None:
    server = Server(("0.0.0.0", PORT), Handler)
    signal.signal(signal.SIGTERM, _stop)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()
