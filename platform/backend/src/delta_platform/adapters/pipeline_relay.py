"""Pipeline board relay that coexists with the production application orchestrator."""

import re
import threading
from http.cookies import SimpleCookie
from urllib.parse import urlsplit

import httpx

from delta_platform.core.errors import Problem

RUN_ID = re.compile(r"^[A-Za-z0-9]{1,128}$")
NODE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,40}$")
COOKIE = "__Host-delta_session"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class OrchestratorRelay:
    def __init__(self, settings, client=None):
        self.base = settings.orchestrator_url.rstrip("/")
        parsed = urlsplit(self.base)
        self.host = getattr(settings, "orchestrator_public_host", "") or (
            parsed.netloc if parsed.scheme == "https" else ""
        )
        self.password = settings.orchestrator_password.get_secret_value()
        self.client = client or httpx.Client(timeout=10, follow_redirects=False, trust_env=False)
        self.cookie = ""
        self.lock = threading.Lock()

    @property
    def configured(self):
        try:
            base = urlsplit(self.base)
            public = urlsplit("https://" + self.host)
            return bool(
                self.password
                and base.scheme in ("http", "https")
                and base.hostname
                and not any((base.username, base.password, base.path, base.query, base.fragment))
                and public.hostname
                and public.netloc == self.host
                and not any(
                    (public.username, public.password, public.path, public.query, public.fragment)
                )
                and not any(c in self.host + self.base for c in "\r\n")
            )
        except ValueError:
            return False

    def _headers(self):
        return {"Host": self.host, "Origin": "https://" + self.host}

    def _login(self):
        response = self.client.post(
            f"{self.base}/login", data={"password": self.password}, headers=self._headers()
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
        headers = {**self._headers(), "Cookie": self.cookie}
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
            raise Problem(
                "pipeline_rejected",
                "파이프라인 상태를 새로 확인한 뒤 다시 요청하세요.",
                response.status_code,
            )
        if response.status_code not in (200, 202):
            raise Problem("pipeline_unavailable", "파이프라인 기록을 읽지 못했습니다.", 502)
        try:
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise ValueError("Response too large")
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("Expected object")
        except ValueError:
            raise Problem(
                "pipeline_unavailable", "파이프라인 응답을 확인하지 못했습니다.", 502
            ) from None
        return result

    def runs(self):
        return self.request("GET", "/api/pipeline/runs")

    def options(self, repo, branch=None):
        status = self.request("GET", "/api/status")
        params = {"repo": repo} | ({"branch": branch} if branch else {})
        refs = self.request("GET", "/api/git/refs", params=params)
        return {
            "repo": refs.get("repo", repo),
            "branch": refs.get("branch"),
            "branches": refs.get("branches", []),
            "commits": refs.get("commits", []),
            "release_targets": status.get("release_targets", []),
            "cutover_configured": bool(status.get("cutover_configured")),
        }

    def start(self, body, key=None):
        return self.request("POST", "/api/pipeline/runs", body, key=key)

    def run(self, run_id):
        if not RUN_ID.fullmatch(run_id):
            raise Problem("run_not_found", "실행 기록을 찾지 못했습니다.", 404)
        return self.request("GET", f"/api/pipeline/runs/{run_id}")

    def log(self, run_id, node_id):
        if not RUN_ID.fullmatch(run_id) or not NODE_ID.fullmatch(node_id):
            raise Problem("run_not_found", "실행 기록을 찾지 못했습니다.", 404)
        return self.request("GET", f"/api/pipeline/runs/{run_id}/live/log/{node_id}")

    def answer(self, run_id, value, decision_id=None, case_id=None):
        if not RUN_ID.fullmatch(run_id):
            raise Problem("run_not_found", "실행 기록을 찾지 못했습니다.", 404)
        body = {"value": value}
        if decision_id:
            body["decision_id"] = decision_id
        if case_id:
            body["case_id"] = case_id
        return self.request("POST", f"/api/pipeline/runs/{run_id}/answer", body)
