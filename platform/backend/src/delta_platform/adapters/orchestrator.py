"""Read-only relay to the delta-orchestrator pipeline board.

The orchestrator only accepts its public Host with an admin session. The platform keeps that
password server-side, logs in once and reuses the session cookie. Browsers never see it.
"""

import re
import threading
from http.cookies import SimpleCookie

import httpx

from delta_platform.core.errors import Problem

RUN_ID = re.compile(r"^[A-Za-z0-9]{1,128}$")
NODE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,40}$")
COOKIE = "__Host-delta_session"  # delta_orchestrator.access.COOKIE


class OrchestratorRelay:
    def __init__(self, settings, client=None):
        self.base = settings.orchestrator_url.rstrip("/")
        self.host = settings.orchestrator_public_host
        self.password = settings.orchestrator_password.get_secret_value()
        self.client = client or httpx.Client(timeout=10, follow_redirects=False)
        self.cookie = ""
        self.lock = threading.Lock()

    @property
    def configured(self):
        return bool(self.base and self.host and self.password)

    def _login(self):
        response = self.client.post(
            f"{self.base}/login", data={"password": self.password}, headers={"Host": self.host}
        )
        if response.status_code != 303:
            raise Problem("pipeline_unavailable", "파이프라인 기록에 연결하지 못했습니다.", 502)
        jar = SimpleCookie()
        for header in response.headers.get_list("set-cookie"):
            jar.load(header)
        if COOKIE not in jar:
            raise Problem("pipeline_unavailable", "파이프라인 기록에 연결하지 못했습니다.", 502)
        self.cookie = f"{COOKIE}={jar[COOKIE].value}"

    def _send(self, method, path, body=None, params=None, key=None):
        headers = {"Host": self.host, "Cookie": self.cookie}
        if key:
            headers["Idempotency-Key"] = key
        return self.client.request(
            method, f"{self.base}{path}", json=body, params=params, headers=headers
        )

    def request(self, method, path, body=None, params=None, key=None):
        if not self.configured:
            raise Problem(
                "pipeline_unconfigured", "파이프라인 기록 연결이 설정되지 않았습니다.", 503
            )
        try:
            with self.lock:
                if not self.cookie:
                    self._login()
                response = self._send(method, path, body, params, key)
                if response.status_code == 401:
                    self._login()
                    response = self._send(method, path, body, params, key)
        except httpx.HTTPError:
            raise Problem(
                "pipeline_unavailable", "파이프라인 기록에 연결하지 못했습니다.", 502
            ) from None
        if response.status_code == 404:
            raise Problem("run_not_found", "실행 기록을 찾지 못했습니다.", 404)
        if response.status_code in (400, 409):
            # The orchestrator's own reasons are fixed server-side messages (e.g. a run is already active).
            try:
                detail = str(response.json().get("detail"))[:200]
            except ValueError:
                detail = "요청을 처리하지 못했습니다."
            raise Problem("pipeline_rejected", detail, response.status_code)
        if response.status_code not in (200, 202):
            raise Problem("pipeline_unavailable", "파이프라인 기록을 읽지 못했습니다.", 502)
        return response

    def runs(self):
        return self.request("GET", "/api/pipeline/runs").json()

    def options(self, repo, branch=None):
        """Start form defaults: branches and recent commits, release targets, cutover availability."""
        status = self.request("GET", "/api/status").json()
        params = {"repo": repo} | ({"branch": branch} if branch else {})
        refs = self.request("GET", "/api/git/refs", params=params).json()
        return {
            "repo": refs.get("repo", repo),
            "branch": refs.get("branch"),
            "branches": refs.get("branches", []),
            "commits": refs.get("commits", []),
            "release_targets": status.get("release_targets", []),
            "cutover_configured": bool(status.get("cutover_configured")),
        }

    def start(self, body, key=None):
        return self.request("POST", "/api/pipeline/runs", body, key=key).json()

    def run(self, run_id):
        if not RUN_ID.match(run_id):
            raise Problem("run_not_found", "실행 기록을 찾지 못했습니다.", 404)
        return self.request("GET", f"/api/pipeline/runs/{run_id}").json()

    def log(self, run_id, node_id):
        if not RUN_ID.match(run_id) or not NODE_ID.match(node_id):
            raise Problem("run_not_found", "실행 기록을 찾지 못했습니다.", 404)
        return self.request("GET", f"/api/pipeline/runs/{run_id}/live/log/{node_id}").json()

    def answer(self, run_id, value, decision_id=None, case_id=None):
        if not RUN_ID.match(run_id):
            raise Problem("run_not_found", "실행 기록을 찾지 못했습니다.", 404)
        body = {"value": value}
        if decision_id:
            body["decision_id"] = decision_id
        if case_id:
            body["case_id"] = case_id
        return self.request("POST", f"/api/pipeline/runs/{run_id}/answer", body).json()
