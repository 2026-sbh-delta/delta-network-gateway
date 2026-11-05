"""에이전트를 띄우기 전에 막힐 곳을 몇 초 안에 원인과 함께 알려 주는지."""

import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from dast_agent import server
from dast_agent.contracts import sample_report
from dast_agent.preflight import PreflightError, check_target


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(int(self.path.strip("/") or 200))
        self.end_headers()

    def log_message(self, *_args):
        pass


@pytest.fixture(scope="module")
def origin():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def test_reachable_target_passes(origin):
    await check_target(origin, "/200")


@pytest.mark.parametrize(("path", "words"), [
    ("/403", ["403", "Cloud Armor", "실행기 공인 IP"]),
    ("/404", ["404", "/404"]),
    ("/502", ["502", "배포 상태"]),
])
async def test_blocked_or_broken_target_says_why(origin, path, words):
    with pytest.raises(PreflightError) as caught:
        await check_target(origin, path)
    assert all(word in str(caught.value) for word in words)


async def test_down_target_fails_within_seconds():
    started = time.monotonic()
    with pytest.raises(PreflightError, match="연결되지 않습니다"):
        await check_target(f"http://127.0.0.1:{closed_port()}", "/board", timeout=2)
    assert time.monotonic() - started < 3


def test_run_stops_at_preflight_without_starting_agents(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_claude_auth", lambda: {"logged_in": True, "method": "test"})
    app = server.create_app(port=18765, target_port=closed_port(), runs_dir=tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:18765") as client:
        token = client.get("/api/config").json()["csrf_token"]
        run_id = client.post("/api/runs", headers={"X-DAST-Token": token}, json={
            "scenario": "vulnerable", "planner": "agent", "report": sample_report(),
        }).json()["run_id"]
        deadline = time.monotonic() + 15
        while (state := client.get(f"/api/runs/{run_id}").json())["status"] == "running":
            assert time.monotonic() < deadline
            time.sleep(0.2)
    assert state["status"] == "error"
    assert state["error_kind"] == "preflight"
    assert {r["status"] for r in state["results"]} == {"INCONCLUSIVE"}
    assert all(r["evidence"]["phase"] == "preflight" for r in state["results"])
