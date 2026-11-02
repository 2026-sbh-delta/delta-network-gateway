#!/usr/bin/env python3
"""시연 콘솔: 화면과 흐름 구동. 로컬 전용이며 토큰은 브라우저로 보내지 않는다.

    python server.py [--port 18100]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, deque
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from delta_pipeline.clients import Dast, DeltaAi, Jenkins, ServiceUnavailable
from delta_pipeline.model import SCENARIOS, Options
from delta_pipeline.run import Run, latest_unfinished

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
JENKINS_ENV = REPO / "deploy" / "jenkins-local" / ".env"
ONPREM_DIR = HERE / "var" / "onprem"
PIPELINE_DIR = HERE / "var" / "pipeline"
ONPREM_CONFIG = Path(os.environ.get("DEMO_ONPREM_CONFIG", REPO.parent / "notes" / "delta-onprem-collect.json"))
COLLECTOR = REPO / "services" / "delta-ai" / "scripts" / "collect_onprem.py"
COLLECTOR_PYTHON = REPO / "services" / "delta-ai" / ".venv" / "bin" / "python"
DELTA_URL = "http://127.0.0.1:18090"
DAST_URL = "http://127.0.0.1:8765"
JENKINS_URL = "http://127.0.0.1:18080"
GATEWAY_URL = "http://127.0.0.1:18081/api/hello"
PLANNERS = ("reviewed", "agent")
ARTIFACT_PATH = re.compile(r"^/dast-artifacts/[0-9a-f-]{36}/[A-Za-z0-9._-]+\.png$")
TRAFFIC_WINDOW_SECONDS = 8
TRAFFIC_INTERVAL_SECONDS = 0.1
COLLECT_TIMEOUT_SECONDS = 900


def read_env(path: Path) -> dict[str, str]:
    pairs = (line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line)
    return {key.strip(): value.strip() for key, value in pairs}


class TrafficWatcher(threading.Thread):
    """게이트웨이로 계속 요청을 보내 버전별 응답을 센다. 전환 중 실패가 있는지 그대로 보여 준다."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self._lock = threading.Lock()
        self._recent: deque[tuple[float, str, bool]] = deque()
        self._total = 0
        self._failed = 0

    def reset(self) -> None:
        with self._lock:
            self._recent.clear()
            self._total = self._failed = 0

    def _probe(self) -> tuple[str, bool]:
        try:
            with urllib.request.urlopen(GATEWAY_URL, timeout=2) as response:
                return response.headers.get("X-App-Version") or "?", False
        except urllib.error.HTTPError as exc:
            return exc.headers.get("X-App-Version") or "?", True
        except (urllib.error.URLError, TimeoutError, OSError):
            return "응답 없음", True

    def run(self) -> None:
        while True:
            version, failed = self._probe()
            now = time.time()
            with self._lock:
                self._recent.append((now, version, failed))
                self._total += 1
                self._failed += failed
                while self._recent and self._recent[0][0] < now - TRAFFIC_WINDOW_SECONDS:
                    self._recent.popleft()
            time.sleep(TRAFFIC_INTERVAL_SECONDS)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            versions = Counter(version for _, version, _ in self._recent)
            recent_failed = sum(failed for *_, failed in self._recent)
            return {"versions": dict(versions), "recent": len(self._recent), "recent_failed": recent_failed,
                    "total": self._total, "failed": self._failed}


class App:
    def __init__(self, env: dict[str, str]) -> None:
        self.delta = DeltaAi(DELTA_URL, env["DELTA_API_TOKEN"])
        self.dast = Dast(DAST_URL)
        self.jenkins = Jenkins(JENKINS_URL, "admin", env["JENKINS_ADMIN_PASSWORD"])
        self.traffic = TrafficWatcher()
        self.traffic.start()
        self.run: Run | None = None
        self.thread: threading.Thread | None = None
        self.collect_state: dict[str, Any] = {"state": "idle"}
        self._lock = threading.Lock()
        self._resume_unfinished()

    def _resume_unfinished(self) -> None:
        """콘솔이 강제 종료됐으면 끝나지 못한 실행을 이어 간다."""
        saved = latest_unfinished(PIPELINE_DIR) if PIPELINE_DIR.exists() else None
        if saved:
            run_id, options = saved
            self._launch(options, run_id)

    def _launch(self, options: Options, run_id: str | None = None) -> None:
        self.run = Run(options, delta=self.delta, dast=self.dast, jenkins=self.jenkins,
                       onprem_dir=ONPREM_DIR, store_dir=PIPELINE_DIR, service_ready=self._service_ready,
                       on_release_start=self.traffic.reset, run_id=run_id)
        self.thread = threading.Thread(target=self.run.execute, daemon=True)
        self.thread.start()

    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self, scenario_id: str, planner: str, rejudge: bool) -> str | None:
        scenario = next((s for s in SCENARIOS if s.id == scenario_id), None)
        if scenario is None or planner not in PLANNERS:
            return "알 수 없는 시나리오나 점검 방식이다"
        with self._lock:
            if self.running():
                return "이미 진행 중이다"
            self.traffic.reset()
            self._launch(Options(scenario, planner, rejudge))
        return None

    def _service_ready(self) -> bool:
        """앱이 응답해야 버전 헤더가 붙는다. HAProxy 의 503 만 있으면 기준 버전이 없는 것이다."""
        time.sleep(1)
        return bool(self.traffic.snapshot()["versions"].keys() - {"응답 없음", "?"})

    def health(self) -> dict[str, Any]:
        def check(call: Any) -> bool:
            try:
                call()
                return True
            except (ServiceUnavailable, KeyError):
                return False

        def delta_ok() -> None:
            with urllib.request.urlopen(f"{DELTA_URL}/healthz", timeout=2):
                pass

        def probe() -> bool:
            try:
                delta_ok()
                return True
            except (urllib.error.URLError, TimeoutError, OSError):
                return False

        return {"delta_ai": probe(), "dast": check(self.dast.config),
                "jenkins": check(self.jenkins.describe_root)}

    def collect(self) -> str | None:
        with self._lock:
            if self.collect_state.get("state") == "running":
                return "이미 수집 중이다"
            if not ONPREM_CONFIG.is_file():
                return "수집 설정 파일이 없다"
            self.collect_state = {"state": "running", "started_at": time.time()}
        threading.Thread(target=self._collect, daemon=True).start()
        return None

    def _collect(self) -> None:
        ONPREM_DIR.mkdir(parents=True, exist_ok=True)
        command = [str(COLLECTOR_PYTHON), str(COLLECTOR), "--config", str(ONPREM_CONFIG), "--out", str(ONPREM_DIR), "--dry-run"]
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=COLLECT_TIMEOUT_SECONDS, cwd=COLLECTOR.parent.parent)
        except subprocess.TimeoutExpired:
            self.collect_state = {"state": "failed", "message": "수집 시간 초과"}
            return
        # 일부 실패(종료 코드 2)는 빈 값으로 판단에 들어가 unknown 이 된다. 숨기지 않고 알린다
        errors = [line for line in done.stderr.splitlines() if line.startswith("수집 실패")]
        state = "done" if done.returncode == 0 else "partial" if done.returncode == 2 else "failed"
        self.collect_state = {"state": state, "errors": errors[:10], "finished_at": time.time()}


def state_payload(app: App) -> dict[str, Any]:
    return {"run": app.run.snapshot() if app.run else None, "traffic": app.traffic.snapshot(), "running": app.running(),
            "collect": app.collect_state,
            "scenarios": [asdict(s) for s in SCENARIOS]}


class Handler(BaseHTTPRequestHandler):
    app: App

    def log_message(self, *_: Any) -> None:
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, value: Any, status: int = 200) -> None:
        self._send(status, json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _local_host(self) -> bool:
        return self.headers.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost")

    def do_GET(self) -> None:
        if not self._local_host():
            self._json({"detail": "허용되지 않은 주소"}, 403)
        elif self.path == "/":
            self._send(200, (HERE / "static" / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/state":
            self._json(state_payload(self.app))
        elif self.path == "/api/health":
            self._json(self.app.health())
        elif ARTIFACT_PATH.match(self.path):
            self._artifact()
        else:
            self._json({"detail": "없는 경로"}, 404)

    def _artifact(self) -> None:
        path = self.path.removeprefix("/dast-artifacts/")
        try:
            with urllib.request.urlopen(f"{DAST_URL}/api/artifacts/{path}", timeout=5) as response:
                self._send(200, response.read(), "image/png")
        except (urllib.error.URLError, TimeoutError, OSError):
            self._json({"detail": "증거 파일을 읽지 못했다"}, HTTPStatus.BAD_GATEWAY)

    def do_POST(self) -> None:
        # 다른 사이트의 폼 전송을 막는다. 브라우저는 교차 출처에서 이 헤더를 붙일 수 없다
        if not self._local_host() or self.headers.get("X-Console") != "1":
            self._json({"detail": "허용되지 않은 요청"}, 403)
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json({"detail": "JSON 이 아니다"}, 400)
            return
        error: str | None
        if self.path == "/api/start":
            error = self.app.start(str(body.get("scenario")), str(body.get("planner", "reviewed")), body.get("rejudge") is True)
        elif self.path == "/api/answer":
            error = None if self.app.run and self.app.run.answer(str(body.get("value"))) else "지금 받을 수 없는 답이다"
        elif self.path == "/api/collect":
            error = self.app.collect()
        else:
            self._json({"detail": "없는 경로"}, 404)
            return
        self._json({"detail": error} if error else {"ok": True}, 409 if error else 200)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18100)
    args = parser.parse_args()
    if not JENKINS_ENV.is_file():
        print(f"{JENKINS_ENV} 가 없다. deploy/jenkins-local/up.sh 를 먼저 실행한다", file=sys.stderr)
        return 1
    Handler.app = App(read_env(JENKINS_ENV))
    print(f"시연 콘솔: http://127.0.0.1:{args.port}")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
