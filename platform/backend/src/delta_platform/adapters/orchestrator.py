"""Read-only relay to the delta-orchestrator pipeline board.

The orchestrator only accepts its public Host with an admin session. The platform keeps that
password server-side, logs in once and reuses the session cookie. Browsers never see it.
"""

import json
import re
import threading
from http.cookies import SimpleCookie
from urllib.parse import urlsplit

import httpx

from delta_platform.core.errors import Problem

RUN_ID = re.compile(r"^[A-Za-z0-9]{1,128}$")
NODE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,40}$")
ARTIFACT = re.compile(r"^[A-Za-z0-9_-]{1,90}\.png$")
COOKIE = "__Host-delta_session"  # delta_orchestrator.access.COOKIE


class OrchestratorRelay:
    def __init__(self, settings, client=None, stream_client=None):
        self.base = settings.orchestrator_url.rstrip("/")
        # Production points straight at the public https address; its host is then the public Host.
        public = urlsplit(self.base)
        self.host = settings.orchestrator_public_host or (
            public.netloc if public.scheme == "https" else ""
        )
        self.password = settings.orchestrator_password.get_secret_value()
        self.client = client or httpx.Client(timeout=10, follow_redirects=False)
        # DAST sends a keep-alive line every 10 seconds; allow a quiet minute before giving up.
        self.stream_client = stream_client or httpx.Client(
            timeout=httpx.Timeout(10, read=60), follow_redirects=False
        )
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

    def _send(self, method, path, body=None, params=None, key=None, timeout=None):
        headers = {"Host": self.host, "Cookie": self.cookie}
        if key:
            headers["Idempotency-Key"] = key
        extra = {"timeout": timeout} if timeout else {}
        return self.client.request(
            method, f"{self.base}{path}", json=body, params=params, headers=headers, **extra
        )

    def request(self, method, path, body=None, params=None, key=None, timeout=None):
        if not self.configured:
            raise Problem(
                "pipeline_unconfigured", "파이프라인 기록 연결이 설정되지 않았습니다.", 503
            )
        try:
            with self.lock:
                if not self.cookie:
                    self._login()
                response = self._send(method, path, body, params, key, timeout)
                if response.status_code == 401:
                    self._login()
                    response = self._send(method, path, body, params, key, timeout)
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
        # Starting checks the repository and the diff request before answering; allow a minute.
        return self.request("POST", "/api/pipeline/runs", body, key=key, timeout=60).json()

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

    def reset_view(self):
        return self.request("GET", "/api/demo/reset").json()

    def reset(self):
        """The click in the dashboard is the human approval the orchestrator asks for."""
        return self.request("POST", "/api/demo/reset", {"approve": True}).json()

    # DAST live view (verdicts, evidence screenshots), its frames/actions stream and the fix proposal.
    def _run_path(self, run_id, suffix):
        if not RUN_ID.match(run_id):
            raise Problem("run_not_found", "실행 기록을 찾지 못했습니다.", 404)
        return f"/api/pipeline/runs/{run_id}{suffix}"

    def dast_view(self, run_id):
        data = self.request("GET", self._run_path(run_id, "/live/dast")).json()
        # Evidence links point at the orchestrator; serve them through this API instead.
        old = f"/api/pipeline/runs/{run_id}/live/dast/artifacts/"
        new = f"/api/v1/pipeline/runs/{run_id}/dast/artifacts/"
        return json.loads(json.dumps(data).replace(old, new))

    def dast_artifact(self, run_id, name):
        if not ARTIFACT.match(name):
            raise Problem("run_not_found", "스크린샷이 없습니다.", 404)
        response = self.request("GET", self._run_path(run_id, f"/live/dast/artifacts/{name}"))
        if not response.headers.get("content-type", "").startswith("image/png"):
            raise Problem("pipeline_unavailable", "스크린샷을 읽지 못했습니다.", 502)
        return response.content

    def dast_fix(self, run_id):
        return self.request("GET", self._run_path(run_id, "/live/dast/fix")).json()

    def dast_events(self, run_id, last_event_id=None):
        """Pass the DAST server-sent events through unchanged; a broken link ends with `event: gone`."""
        path = self._run_path(run_id, "/live/dast/events")
        if not self.configured:
            yield b"event: gone\ndata: 503\n\n"
            return
        try:
            with self.lock:
                if not self.cookie:
                    self._login()
        except (Problem, httpx.HTTPError):
            yield b"event: gone\ndata: 502\n\n"
            return
        headers = {"Host": self.host, "Cookie": self.cookie, "Accept": "text/event-stream"}
        if last_event_id and last_event_id.isdigit():
            headers["Last-Event-ID"] = last_event_id
        try:
            with self.stream_client.stream("GET", f"{self.base}{path}", headers=headers) as r:
                if r.status_code != 200:
                    if r.status_code == 401:
                        self.cookie = ""
                    yield f"event: gone\ndata: {r.status_code}\n\n".encode()
                    return
                yield from r.iter_bytes()
        except httpx.HTTPError:
            yield b"event: gone\ndata: 502\n\n"
