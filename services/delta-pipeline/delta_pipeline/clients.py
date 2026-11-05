"""파이프라인이 부르는 서비스(SAST·DAST·Jenkins·delta-ai)의 HTTP 호출. 표준 라이브러리만 쓴다."""

from __future__ import annotations

import base64
import hashlib
import http.cookiejar
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any

JUDGE_TIMEOUT_SECONDS = 180  # delta-ai 의 LLM 제한(60초 × 2회)보다 길어야 한다
DEFAULT_TIMEOUT_SECONDS = 15
PR_TIMEOUT_SECONDS = 120  # 원격 저장소 받기·푸시·PR 생성
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


class Sast:
    """같은 run_id·같은 입력으로 다시 시작하면 기존 점검을 돌려준다(재개해도 두 번 돌지 않는다)."""

    def __init__(self, url: str, token: str) -> None:
        self._url, self._headers = url, {"Authorization": f"Bearer {token}"}

    def _get(self, path: str) -> Any:
        return _json(urllib.request.Request(self._url + path, headers=self._headers), timeout=DEFAULT_TIMEOUT_SECONDS)

    def start(self, run_id: str, repo: str, commit: str) -> dict[str, Any]:
        body = {"run_id": run_id, "repo": repo, "commit": commit, "mode": "fast"}
        request = urllib.request.Request(f"{self._url}/v1/sast/scans", json.dumps(body).encode(), method="POST",
                                         headers={**self._headers, "Content-Type": "application/json"})
        return _json(request, timeout=DEFAULT_TIMEOUT_SECONDS)

    def scan(self, run_id: str) -> dict[str, Any]:
        return self._get(f"/v1/sast/scans/{run_id}")

    def sarif(self, run_id: str) -> dict[str, Any]:
        return self._get(f"/v1/sast/scans/{run_id}/sarif")


def evidence_hash(value: Any) -> str:
    """DAST 플랫폼 API 의 근거 해시와 같은 방식(정렬한 키, 공백 없는 JSON 의 SHA-256)."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Dast:
    """DAST 플랫폼 API(/api/platform/runs). 내부 주소 + 플랫폼 토큰.

    같은 점검 ID·같은 입력은 같은 점검을 돌려준다(재개해도 두 번 돌지 않는다). 입력이 다르면 409.
    점검 서버가 재시작돼 사라진 점검은 interrupted 로 남고 자동으로 다시 돌지 않는다.
    """

    def __init__(self, url: str, token: str) -> None:
        self._url, self._headers = url, {"Authorization": f"Bearer {token}"}

    def submit(self, run_id: str, binding: dict[str, Any], report: dict[str, Any],
               reverify_of: str | None = None) -> dict[str, Any]:
        body = {"binding": binding, "report": report, **({"reverify_of": reverify_of} if reverify_of else {})}
        request = urllib.request.Request(f"{self._url}/api/platform/runs/{run_id}", json.dumps(body).encode(),
                                         method="PUT", headers={**self._headers, "Content-Type": "application/json"})
        return _json(request, timeout=DEFAULT_TIMEOUT_SECONDS)

    def view(self, run_id: str) -> dict[str, Any]:
        return _json(urllib.request.Request(f"{self._url}/api/platform/runs/{run_id}", headers=self._headers),
                     timeout=DEFAULT_TIMEOUT_SECONDS)

    def fix(self, run_id: str, finding_id: str) -> dict[str, Any]:
        """재현 직후 DAST 가 만든 수정안(패치·코드 흐름). 아직 만드는 중이면 status=generating."""
        path = f"/api/runs/{run_id}/findings/{urllib.parse.quote(finding_id, safe='')}/fix"
        return _json(urllib.request.Request(self._url + path, headers=self._headers), timeout=DEFAULT_TIMEOUT_SECONDS)

    def open_pr(self, run_id: str, finding_id: str, base: str | None = None) -> dict[str, Any]:
        """사람이 '수정 적용'을 고른 뒤에만 부른다. 수정 브랜치를 원격에 올리고 PR 을 만든다(커밋 SHA 를 돌려준다)."""
        path = f"/api/runs/{run_id}/findings/{urllib.parse.quote(finding_id, safe='')}/pr"
        request = urllib.request.Request(self._url + path, json.dumps({"approve": True, **({"base": base} if base else {})}).encode(), method="POST",
                                         headers={**self._headers, "Content-Type": "application/json"})
        return _json(request, timeout=PR_TIMEOUT_SECONDS)


class Staging:
    """스테이징 도구(조회 토큰). 점검을 끝낸 실행이 자기 점유를 푼다."""

    def __init__(self, url: str, token: str) -> None:
        self._url, self._headers = url, {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def release(self, cloud: str, holder: str) -> bool:
        request = urllib.request.Request(f"{self._url}/v1/staging/leases/{cloud}/release",
                                         json.dumps({"holder": holder}).encode(), method="POST", headers=self._headers)
        return bool(_json(request, timeout=DEFAULT_TIMEOUT_SECONDS).get("released"))


class Jenkins:
    def __init__(self, url: str, user: str, password: str, *, job: str) -> None:
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

    def trigger(self, params: Mapping[str, str], run_key: str) -> int:
        """빌드를 시작하고 빌드 번호를 돌려준다. RUN_ID 는 재개 때 같은 빌드를 찾는 키다."""
        body = urllib.parse.urlencode({**params, "RUN_ID": run_key}).encode()
        return self._wait_started(self._post(f"/job/{self._job}/buildWithParameters", body))

    def status(self, build: int) -> str:
        """진행 중이면 IN_PROGRESS, 끝났으면 Jenkins 결과(SUCCESS·FAILURE·ABORTED…). 플러그인 없이 기본 API만 쓴다."""
        info = self._get(f"/job/{self._job}/{build}/api/json?tree=building,result")
        return "IN_PROGRESS" if info.get("building") or not info.get("result") else str(info["result"])

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


class Cloudflare:
    """Cloudflare API v4(DNS 레코드만). 토큰은 해당 zone 의 DNS 편집 권한만 가진 것을 쓴다.

    호원님 스크립트(11/5)의 검증을 옮겼다. 외부 CLI 대신 API 를 직접 부른다(오케스트레이터 이미지에 Node 가 없다).
    """

    API = "https://api.cloudflare.com/client/v4"

    def __init__(self, token: str) -> None:
        self._headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        request = urllib.request.Request(self.API + path, None if body is None else json.dumps(body).encode(),
                                         method=method, headers=self._headers)
        reply = _json(request, timeout=DEFAULT_TIMEOUT_SECONDS)
        if not reply.get("success"):
            raise ServiceUnavailable(f"Cloudflare 오류: {[e.get('message') for e in reply.get('errors', [])][:3]}")
        return reply["result"]

    def find_record(self, zone_id: str, name: str) -> dict[str, Any]:
        records = self._call("GET", f"/zones/{zone_id}/dns_records?" + urllib.parse.urlencode({"name": name}))
        if len(records) != 1:
            raise ServiceUnavailable(f"{name} 레코드가 하나가 아니다({len(records)}개)")
        return records[0]

    def set_content(self, zone_id: str, record_id: str, content: str) -> dict[str, Any]:
        return self._call("PATCH", f"/zones/{zone_id}/dns_records/{record_id}", {"content": content})


def probe(url: str, *, host: str | None = None, timeout: float = 10) -> tuple[int, str]:
    """HTTP 상태와 X-Served-By 를 돌려준다. host 를 주면 원본에 서비스 도메인 Host 로 직접 묻는다."""
    request = urllib.request.Request(url, headers={"Host": host} if host else {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 설정한 주소만
            return response.status, response.headers.get("X-Served-By", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("X-Served-By", "") if exc.headers else ""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ServiceUnavailable(f"{type(exc).__name__}: {url}") from exc


def resolve_cname(name: str, *, timeout: float = 10) -> str | None:
    """공개 리졸버(DoH)가 실제로 돌려주는 CNAME 대상. 설정(API)만이 아니라 밖에서 보이는 값을 확인한다."""
    url = "https://cloudflare-dns.com/dns-query?" + urllib.parse.urlencode({"name": name, "type": "CNAME"})
    reply = _json(urllib.request.Request(url, headers={"Accept": "application/dns-json"}), timeout=timeout)
    answers = [a.get("data", "") for a in reply.get("Answer", ()) if a.get("type") == 5]
    return answers[0].rstrip(".").lower() if answers else None
