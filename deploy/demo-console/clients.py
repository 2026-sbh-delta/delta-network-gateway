"""시연 콘솔이 부르는 세 서비스의 HTTP 호출. 표준 라이브러리만 쓴다."""

from __future__ import annotations

import base64
import http.cookiejar
import json
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

JUDGE_TIMEOUT_SECONDS = 180  # delta-ai 의 LLM 제한(60초 × 2회)보다 길어야 한다
DEFAULT_TIMEOUT_SECONDS = 15
JOB = "delta-demo-deploy"
JENKINS_CONTAINER = "delta-jenkins-local-jenkins-1"
WORKSPACE = f"/var/jenkins_home/workspace/{JOB}"
QUEUE_WAIT_SECONDS = 120


class ServiceUnavailable(RuntimeError):
    """호출한 서비스에서 판정이나 결과를 받지 못했다. 통과로 읽지 않는다."""


def _json(request: urllib.request.Request, *, timeout: float, opener: Any = urllib.request.urlopen) -> Any:
    try:
        with opener(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise ServiceUnavailable(f"HTTP {exc.code}: {request.full_url}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ServiceUnavailable(f"{type(exc).__name__}: {request.full_url}") from exc
    try:
        return json.loads(body) if body else {}
    except json.JSONDecodeError as exc:
        raise ServiceUnavailable(f"JSON 아님: {request.full_url}") from exc


class DeltaAi:
    def __init__(self, url: str, token: str) -> None:
        self._url, self._token = url, token

    def assess(self, body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(f"{self._url}/v1/assessments", json.dumps(body).encode(), method="POST",
                                         headers={"Authorization": f"Bearer {self._token}",
                                                  "Content-Type": "application/json"})
        return _json(request, timeout=JUDGE_TIMEOUT_SECONDS)


class Dast:
    """토큰은 서버를 시작할 때마다 바뀌므로 호출할 때마다 읽는다."""

    def __init__(self, url: str) -> None:
        self._url = url

    def _get(self, path: str) -> Any:
        return _json(urllib.request.Request(self._url + path), timeout=DEFAULT_TIMEOUT_SECONDS)

    def config(self) -> dict[str, Any]:
        return self._get("/api/config")

    def start(self, scenario: str, planner: str, report: dict[str, Any]) -> str:
        token = self.config()["csrf_token"]
        body = {"scenario": scenario, "planner": planner, "concurrency": 1, "impact": True, "report": report}
        request = urllib.request.Request(f"{self._url}/api/runs", json.dumps(body).encode(), method="POST",
                                         headers={"X-DAST-Token": token, "Content-Type": "application/json"})
        return _json(request, timeout=DEFAULT_TIMEOUT_SECONDS)["run_id"]

    def run(self, run_id: str) -> dict[str, Any]:
        return self._get(f"/api/runs/{run_id}")


class Jenkins:
    def __init__(self, url: str, user: str, password: str) -> None:
        self._url = url
        self._auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
        # 크럼은 발급받은 세션에서만 유효하다
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def _request(self, path: str, *, data: bytes | None = None, headers: dict[str, str] | None = None
                 ) -> urllib.request.Request:
        return urllib.request.Request(self._url + path, data=data, method="POST" if data is not None else "GET",
                                      headers={"Authorization": self._auth, **(headers or {})})

    def _get(self, path: str) -> Any:
        return _json(self._request(path), timeout=DEFAULT_TIMEOUT_SECONDS, opener=self._opener.open)

    def _post(self, path: str, data: bytes = b"") -> str:
        crumb = self._get("/crumbIssuer/api/json")
        request = self._request(path, data=data, headers={crumb["crumbRequestField"]: crumb["crumb"]})
        try:
            with self._opener.open(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
                return response.headers.get("Location", "")
        except urllib.error.HTTPError as exc:
            # Jenkins 는 입력 처리 뒤 302 를 준다
            if exc.code in (301, 302, 303):
                return exc.headers.get("Location", "")
            raise ServiceUnavailable(f"HTTP {exc.code}: {path}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ServiceUnavailable(f"{type(exc).__name__}: {path}") from exc

    def describe_root(self) -> dict[str, Any]:
        return self._get(f"/job/{JOB}/api/json?tree=name")

    def _wait_started(self, location: str) -> int:
        queue_path = urllib.parse.urlparse(location).path
        if "/queue/item/" not in queue_path:
            raise ServiceUnavailable("Jenkins 가 큐 위치를 돌려주지 않았다")
        for _ in range(QUEUE_WAIT_SECONDS):
            item = self._get(f"{queue_path.rstrip('/')}/api/json")
            if item.get("executable"):
                return int(item["executable"]["number"])
            if item.get("cancelled"):
                raise ServiceUnavailable("Jenkins 큐에서 취소됐다")
            time.sleep(1)
        raise ServiceUnavailable("Jenkins 빌드가 시작되지 않았다")

    def trigger(self, scenario: str) -> int:
        """빌드를 시작하고 빌드 번호를 돌려준다."""
        query = urllib.parse.urlencode({"SCENARIO": scenario})
        return self._wait_started(self._post(f"/job/{JOB}/buildWithParameters?{query}"))

    def describe(self, build: int) -> dict[str, Any]:
        return self._get(f"/job/{JOB}/{build}/wfapi/describe")

    def pending_inputs(self, build: int) -> list[dict[str, Any]]:
        return self._get(f"/job/{JOB}/{build}/wfapi/pendingInputActions")

    def answer_input(self, build: int, input_id: str, *, approve: bool) -> None:
        action = "proceedEmpty" if approve else "abort"
        self._post(f"/job/{JOB}/{build}/input/{input_id}/{action}")

    def file(self, build: int, name: str) -> dict[str, Any] | None:
        """끝난 빌드는 산출물에서 읽는다. 진행 중에는 Pipeline 작업 폴더를 HTTP 로 읽을 수 없어 컨테이너에서 읽는다."""
        try:
            return self._get(f"/job/{JOB}/{build}/artifact/{name}")
        except ServiceUnavailable:
            return self._workspace_file(name)

    def _workspace_file(self, name: str) -> dict[str, Any] | None:
        done = subprocess.run(["docker", "exec", JENKINS_CONTAINER, "cat", f"{WORKSPACE}/{name}"],
                              capture_output=True, timeout=DEFAULT_TIMEOUT_SECONDS, check=False)
        if done.returncode != 0:
            return None
        try:
            return json.loads(done.stdout)
        except json.JSONDecodeError:
            return None  # 쓰는 중인 파일이다. 다음 조회에서 다시 읽는다

    def console_url(self, build: int) -> str:
        return f"{self._url}/job/{JOB}/{build}/"
