"""파이프라인이 부르는 세 서비스의 HTTP 호출. 표준 라이브러리만 쓴다."""

from __future__ import annotations

import base64
import http.cookiejar
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

JUDGE_TIMEOUT_SECONDS = 180  # delta-ai 의 LLM 제한(60초 × 2회)보다 길어야 한다
DEFAULT_TIMEOUT_SECONDS = 15
JOB = "delta-demo-deploy"
QUEUE_WAIT_SECONDS = 120
BUILD_SEARCH_LIMIT = 50
HTTP_NOT_FOUND = 404


class ServiceUnavailable(RuntimeError):
    """호출한 서비스에서 판정이나 결과를 받지 못했다. 통과로 읽지 않는다."""


class NotFound(ServiceUnavailable):
    """서비스는 응답했고 그런 항목이 없다고 답했다."""


def _json(request: urllib.request.Request, *, timeout: float, opener: Any = urllib.request.urlopen) -> Any:
    try:
        with opener(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        error = NotFound if exc.code == HTTP_NOT_FOUND else ServiceUnavailable
        raise error(f"HTTP {exc.code}: {request.full_url}") from exc
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
    def __init__(self, url: str, user: str, password: str, job: str = JOB) -> None:
        self._url, self._job = url, job
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
        return self._get(f"/job/{self._job}/api/json?tree=name")

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

    def trigger(self, scenario: str, run_key: str) -> int:
        """빌드를 시작하고 빌드 번호를 돌려준다."""
        query = urllib.parse.urlencode({"SCENARIO": scenario, "RUN_ID": run_key})
        return self._wait_started(self._post(f"/job/{self._job}/buildWithParameters?{query}"))

    def find_build(self, run_key: str) -> int | None:
        """이미 시작했거나 큐에 있는 빌드를 찾는다. 재개할 때 같은 배포를 두 번 시작하지 않으려는 것이다."""
        queue = self._get("/queue/api/json?" + urllib.parse.urlencode({"tree": "items[id,params,task[name]]"}))
        for item in queue.get("items", ()):
            if (item.get("task") or {}).get("name") == self._job and f"RUN_ID={run_key}\n" in f"{item.get('params', '')}\n":
                return self._wait_started(f"/queue/item/{item['id']}/")
        tree = f"builds[number,actions[parameters[name,value]]]{{0,{BUILD_SEARCH_LIMIT}}}"
        builds = self._get(f"/job/{self._job}/api/json?" + urllib.parse.urlencode({"tree": tree}))
        for build in builds.get("builds", ()):
            parameters = (p for action in build.get("actions", ()) for p in action.get("parameters", ()))
            if any(p.get("name") == "RUN_ID" and p.get("value") == run_key for p in parameters):
                return int(build["number"])
        return None

    def describe(self, build: int) -> dict[str, Any]:
        return self._get(f"/job/{self._job}/{build}/wfapi/describe")

    def pending_inputs(self, build: int) -> list[dict[str, Any]]:
        return self._get(f"/job/{self._job}/{build}/wfapi/pendingInputActions")

    def answer_input(self, build: int, input_id: str, *, approve: bool) -> None:
        action = "proceedEmpty" if approve else "abort"
        self._post(f"/job/{self._job}/{build}/input/{input_id}/{action}")

    def file(self, build: int, name: str) -> dict[str, Any] | None:
        """파이프라인이 단계마다 올린 산출물을 읽는다. 아직 없으면 None 이다. 조회 실패는 없는 것으로 치지 않는다."""
        try:
            return self._get(f"/job/{self._job}/{build}/artifact/{name}")
        except NotFound:
            return None

    def console_url(self, build: int) -> str:
        return f"{self._url}/job/{self._job}/{build}/"
