"""플랫폼 메인 페이지(임시). 서비스·스테이징·Jenkins·DAST 상태를 한 화면에 모은다.

LangGraph 파이프라인이 붙기 전까지 상태 확인용이다. 모든 조회는 플랫폼 내부망(사설 이름)으로만 한다.

환경 변수
- ORCH_PUBLIC_ORIGIN, ORCH_ADMIN_PASSWORD, ORCH_SESSION_SECRET: 공개 주소 로그인(access.py)
- JENKINS_PLATFORM_PASSWORD: Jenkins 'platform' 사용자(읽기·빌드 권한만)
- DELTA_STAGING_TOKEN: 스테이징 도구 토큰
- DELTA_PUBLIC_URLS(JSON), DELTA_NAT_IP: 화면에 보여 줄 공개 주소와 출구 IP
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from urllib.parse import parse_qs

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles

from delta_pipeline.model import REPO_PREFIX

from . import live
from .access import COOKIE, Access, login_page
from . import pipelines as pipelines_mod
from .pipelines import Pipelines

STATIC = Path(__file__).parent / "static"
ZONE = os.environ.get("DELTA_INTERNAL_ZONE", "cp.internal")


def url(name: str, port: int, path: str) -> str:
    return f"http://{name}.{ZONE}:{port}{path}"


# 화면에 보이는 순서. kind: 실제 서비스(app) / 개발 전 임시 서버(placeholder)
SERVICES = [
    {"id": "orchestrator", "label": "오케스트레이터", "role": "파이프라인 · 이 화면", "health": None},
    {"id": "jenkins", "label": "Jenkins 컨트롤러", "role": "빌드·배포 지시", "health": url("jenkins", 8080, "/login")},
    {"id": "staging", "label": "스테이징 도구", "role": "스테이징 배포 · 스캐너 허용", "health": url("jenkins", 8091, "/healthz")},
    {"id": "jenkins-agent", "label": "빌드 에이전트", "role": "저장소 코드 빌드", "health": None},
    {"id": "sast", "label": "SAST", "role": "소스 점검", "health": url("sast", 8790, "/healthz")},
    {"id": "dast", "label": "DAST", "role": "스테이징 점검", "health": url("dast", 8765, "/healthz")},
    {"id": "diff", "label": "차분 에이전트", "role": "AWS ↔ GCP 비교", "health": url("diff", 8095, "/healthz")},
]


async def probe(client: httpx.AsyncClient, target: str) -> dict:
    started = time.monotonic()
    try:
        r = await client.get(target, timeout=4)
        ms = int((time.monotonic() - started) * 1000)
        return {"state": "up" if r.status_code < 500 else "error", "code": r.status_code, "ms": ms, "placeholder": False}
    except httpx.HTTPError as exc:
        return {"state": "down", "error": type(exc).__name__}


async def placeholder_of(client: httpx.AsyncClient, name: str, port: int) -> bool:
    try:
        r = await client.get(url(name, port, "/"), timeout=3)
        return r.headers.get("content-type", "").startswith("application/json") and r.json().get("status") == "placeholder"
    except (httpx.HTTPError, ValueError):
        return False


async def jenkins_state(client: httpx.AsyncClient) -> dict:
    password = os.environ.get("JENKINS_PLATFORM_PASSWORD")
    if not password:
        return {"error": "Jenkins 계정이 설정되지 않았습니다"}
    auth = ("platform", password)
    base = url("jenkins", 8080, "")
    try:
        nodes, build = await asyncio.gather(
            client.get(base + "/computer/api/json?tree=computer[displayName,offline,idle]", auth=auth, timeout=5),
            client.get(base + "/job/delta-build/lastBuild/api/json?tree=number,result,building,timestamp,duration",
                       auth=auth, timeout=5),
        )
        agents = [c for c in nodes.json().get("computer", []) if c.get("displayName") != "Built-In Node"]
        last = build.json() if build.status_code == 200 else None
        return {"agents": [{"name": a["displayName"], "online": not a.get("offline")} for a in agents],
                "last_build": last}
    except (httpx.HTTPError, ValueError) as exc:
        return {"error": type(exc).__name__}


async def staging_state(client: httpx.AsyncClient) -> dict:
    token = os.environ.get("DELTA_STAGING_TOKEN")
    if not token:
        return {"error": "스테이징 도구 토큰이 없습니다"}
    headers = {"Authorization": f"Bearer {token}"}
    try:
        targets, deployments = await asyncio.gather(
            client.get(url("jenkins", 8091, "/v1/staging/targets"), headers=headers, timeout=25),
            client.get(url("jenkins", 8091, "/v1/staging/deployments"), headers=headers, timeout=5),
        )
        items = deployments.json()
        items = items.get("deployments", items) if isinstance(items, dict) else items
        recent = sorted(items, key=lambda d: d.get("started_at") or "", reverse=True)[:5]
        keep = ("run_id", "cloud", "state", "url", "image_digest", "scanner_ip", "started_at", "ready_at", "error")
        return {"targets": targets.json().get("targets", []), "recent": [{k: d.get(k) for k in keep} for d in recent]}
    except (httpx.HTTPError, ValueError) as exc:
        return {"error": type(exc).__name__}


async def dast_state(client: httpx.AsyncClient) -> dict:
    try:
        r = await client.get(url("dast", 8765, "/api/runs"), timeout=5)
        runs = r.json().get("runs", []) if r.status_code == 200 else []
        keep = ("run_id", "status", "project_id", "started_at", "duration_ms", "counts", "error_kind", "source_label")
        return {"recent": [{k: run.get(k) for k in keep} for run in runs[:5]], "code": r.status_code}
    except (httpx.HTTPError, ValueError) as exc:
        return {"error": type(exc).__name__}


async def collect() -> dict:
    async with httpx.AsyncClient() as client:
        probes = await asyncio.gather(*(probe(client, s["health"]) if s["health"] else asyncio.sleep(0, None)
                                        for s in SERVICES))
        diff_placeholder = await placeholder_of(client, "diff", 8095)
        jenkins, staging, dast = await asyncio.gather(jenkins_state(client), staging_state(client), dast_state(client))
    services = []
    for svc, state in zip(SERVICES, probes):
        entry = {k: svc[k] for k in ("id", "label", "role")}
        if svc["id"] == "orchestrator":
            entry.update(state="up", placeholder=False)
        elif svc["id"] == "jenkins-agent":
            agents = jenkins.get("agents") or []
            online = bool(agents) and all(a["online"] for a in agents)
            entry.update(state="up" if online else ("down" if agents else "unknown"), placeholder=False)
        else:
            entry.update(state)
        services.append(entry)
    # 로그는 별도 서버 없이 플랫폼 중앙 CloudWatch(/delta-cp/…)에 모은다(11/5 결정)
    for svc in services:
        if svc["id"] == "diff":
            svc["placeholder"] = diff_placeholder
    return {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "services": services,
        "jenkins": jenkins,
        "staging": staging,
        "dast": dast,
        "public_urls": json.loads(os.environ.get("DELTA_PUBLIC_URLS", "{}") or "{}"),
        "nat_ip": os.environ.get("DELTA_NAT_IP"),
        # 도메인 전환 설정·토큰이 모두 있을 때만 화면에 전환 선택을 보인다
        "cutover_configured": bool(os.environ.get("DELTA_CUTOVER") and os.environ.get("CLOUDFLARE_API_TOKEN")),
        "release_targets": [{"id": k, "cloud": v.get("cloud"), "label": v.get("label") or k}
                            for k, v in sorted(pipelines_mod.release_targets().items())]
        if pipelines_mod.release_signing_key() else [],
    }


LOG = logging.getLogger(__name__)
# 파이프라인 노드 이벤트·경고를 표준 출력으로 낸다(컨테이너 로그 드라이버가 플랫폼 중앙 CloudWatch 로 보낸다)
logging.basicConfig(level=logging.INFO, format="%(message)s")
GITHUB_API = "https://api.github.com"
REPO_RE = re.compile(re.escape(REPO_PREFIX) + r"([A-Za-z0-9._-]+?)(?:\.git)?/?")
BRANCH_RE = re.compile(r"[A-Za-z0-9._/-]{1,100}")
_refs_cache: dict[tuple[str, str], tuple[float, dict]] = {}


async def git_refs(client: httpx.AsyncClient, repo: str, branch: str | None) -> dict:
    """시작 화면의 브랜치·커밋 선택지. 허용된 조직의 저장소만, GitHub 공개 API 로 읽는다(쓰기 없음).

    같은 질의는 30초 동안 다시 묻지 않는다(비인증 호출은 IP 당 시간 60회 제한)."""
    match = REPO_RE.fullmatch(repo.strip())
    if not match or (branch is not None and (not BRANCH_RE.fullmatch(branch) or ".." in branch)):
        raise HTTPException(400, f"{REPO_PREFIX} 아래 저장소 주소만 받습니다")
    name = match.group(1)
    key = (name, branch or "")
    cached = _refs_cache.get(key)
    if cached and time.monotonic() - cached[0] < 30:
        return cached[1]
    base = f"{GITHUB_API}/repos/{REPO_PREFIX.rstrip('/').rsplit('/', 1)[-1]}/{name}"
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    try:
        info, branches = await asyncio.gather(client.get(base, headers=headers, timeout=8),
                                              client.get(base + "/branches?per_page=50", headers=headers, timeout=8))
        if info.status_code == 404:
            raise HTTPException(404, "저장소를 찾지 못했습니다")
        info.raise_for_status()
        branches.raise_for_status()
        default = info.json().get("default_branch") or "main"
        names = [b["name"] for b in branches.json()]
        chosen = branch if branch in names else default
        commits = await client.get(base + "/commits", params={"sha": chosen, "per_page": 15}, headers=headers, timeout=8)
        commits.raise_for_status()
    except httpx.HTTPStatusError as exc:
        LOG.warning("GitHub 조회 실패 %s: HTTP %s", name, exc.response.status_code)
        raise HTTPException(502, f"GitHub 조회 실패(HTTP {exc.response.status_code})") from None
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        LOG.warning("GitHub 조회 실패 %s: %s", name, type(exc).__name__)
        raise HTTPException(502, f"GitHub 조회 실패({type(exc).__name__})") from None
    result = {"repo": f"{REPO_PREFIX}{name}", "default_branch": default, "branch": chosen,
              # 최근 커밋이 있는 브랜치부터 보이게 기본 브랜치를 맨 앞에 둔다
              "branches": sorted(names, key=lambda n: (n != default, n)),
              "commits": [{"sha": c["sha"], "message": (c["commit"]["message"] or "").split("\n", 1)[0][:120],
                           "author": (c["commit"].get("author") or {}).get("name", "")[:60],
                           "date": (c["commit"].get("author") or {}).get("date")} for c in commits.json()]}
    _refs_cache[key] = (time.monotonic(), result)
    return result


def create_app(pipelines: Pipelines | None = None) -> FastAPI:
    access = Access.from_env()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    if pipelines is None and os.environ.get("ORCH_STATE_DIR"):
        pipelines = Pipelines(os.environ["ORCH_STATE_DIR"])

    def secured(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        return response

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if path == "/healthz" and request.method in {"GET", "HEAD"}:
            return PlainTextResponse("ok", headers={"Cache-Control": "no-store"})
        host = request.headers.get("host", "")
        origin = request.headers.get("origin")
        # 로컬 예외는 실제 접속 주소로 판단한다(Host 헤더는 호출자가 바꿀 수 있다). testclient 는 시험 도구의 가상 주소.
        local = host in {"127.0.0.1:8080", "localhost:8080"} and bool(request.client) and \
            request.client.host in {"127.0.0.1", "::1", "testclient"}
        if not access.is_public(host) and not local:
            return JSONResponse({"detail": "허용되지 않은 요청 출처입니다."}, status_code=403)
        expected = access.public_origin if access.is_public(host) else f"http://{host}"
        # 다른 사이트에서 넘어온 요청은 막되, 링크로 들어오는 페이지 이동(GET 화면 열기)은 허용한다.
        # 상태를 바꾸는 요청(POST 등)과 화면 안의 fetch 는 여전히 같은 출처만 받는다.
        cross_site = request.headers.get("sec-fetch-site") == "cross-site" and not (
            request.method in {"GET", "HEAD"} and request.headers.get("sec-fetch-mode") == "navigate")
        if (origin is not None and origin != expected) or cross_site:
            return JSONResponse({"detail": "허용되지 않은 요청 출처입니다."}, status_code=403)
        if access.is_public(host):
            if path == "/login":
                if request.method != "POST":
                    return secured(HTMLResponse(login_page()))
                if access.locked():
                    return secured(HTMLResponse(login_page("잠시 뒤 다시 시도하세요."), status_code=429))
                given = parse_qs((await request.body())[:4096].decode("utf-8", "replace")).get("password", [""])[0]
                if not access.check_password(given):
                    return secured(HTMLResponse(login_page("비밀번호가 맞지 않습니다."), status_code=401))
                response = RedirectResponse("/", status_code=303)
                response.headers["Set-Cookie"] = access.cookie(access.issue())
                return secured(response)
            if path == "/logout" and request.method == "POST":
                response = RedirectResponse("/login", status_code=303)
                response.headers["Set-Cookie"] = access.cookie("", max_age=0)
                return secured(response)
            if not access.valid(request.cookies.get(COOKIE)):
                if request.method == "GET" and not path.startswith(("/api/", "/static/")):
                    return secured(RedirectResponse("/login", status_code=303))
                return secured(JSONResponse({"detail": "로그인이 필요합니다."}, status_code=401))
        return secured(await call_next(request))

    @app.get("/api/status")
    async def status():
        return await collect()

    def need_pipelines() -> Pipelines:
        if pipelines is None:
            raise HTTPException(503, "파이프라인 상태 디렉터리(ORCH_STATE_DIR)가 설정되지 않았습니다")
        return pipelines

    async def json_body(request: Request) -> dict:
        try:
            body = json.loads((await request.body())[:4096] or b"{}")
        except ValueError:
            raise HTTPException(400, "JSON 본문이 필요합니다")
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON 객체가 필요합니다")
        return body

    @app.get("/api/git/refs")
    async def refs(repo: str, branch: str | None = None):
        async with httpx.AsyncClient() as client:
            return await git_refs(client, repo, branch)

    @app.post("/api/pipeline/runs", status_code=202)
    async def start_run(request: Request):
        body = await json_body(request)
        repo, commit, cutover = body.get("repo"), body.get("commit"), body.get("cutover", False)
        branch = body.get("branch") or ""
        release_target = body.get("release_target") or None
        if release_target is not None and release_target not in pipelines_mod.release_targets():
            raise HTTPException(400, "등록되지 않은 운영 배포 대상입니다")
        if not isinstance(repo, str) or not isinstance(commit, str) or not isinstance(cutover, bool):
            raise HTTPException(400, "repo와 commit(40자리 SHA)이 필요합니다(cutover는 true/false)")
        if not isinstance(branch, str) or (branch and (not BRANCH_RE.fullmatch(branch) or ".." in branch)):
            raise HTTPException(400, "브랜치 이름 형식이 아닙니다")
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            # 실행 기록을 만들기 전에 거른다(커밋 목록을 불러오기 전에 누른 경우 등)
            raise HTTPException(400, "커밋을 고르세요(40자리 SHA)")
        if cutover and not (os.environ.get("DELTA_CUTOVER") and os.environ.get("CLOUDFLARE_API_TOKEN")):
            raise HTTPException(400, "서비스 도메인 전환 설정(대상 레코드·운영 진입점)이 아직 없습니다")
        try:
            return {"run_id": need_pipelines().start(repo, commit, cutover=cutover, branch=branch,
                                                     release_target=release_target)}
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))

    @app.get("/api/pipeline/runs")
    async def list_runs():
        p = need_pipelines()
        return {"runs": p.list(), "active": p.run.run_id if p.running() and p.run else None}

    @app.get("/api/pipeline/runs/{run_id}")
    async def get_run(run_id: str):
        run = need_pipelines().get(run_id)
        if run is None:
            raise HTTPException(404, "없는 실행입니다")
        return run

    @app.post("/api/pipeline/runs/{run_id}/answer")
    async def answer(run_id: str, request: Request):
        value = (await json_body(request)).get("value")
        if not isinstance(value, str) or not need_pipelines().answer(run_id, value):
            raise HTTPException(409, "지금 받을 수 없는 답입니다")
        return {"ok": True}

    # ── 진행 보기: 실행 기록에 남은 빌드 번호·점검 ID 로만 각 도구의 진행 정보를 받아 넘긴다(live.py)
    def board_of(run_id: str) -> dict:
        board = need_pipelines().get(run_id)
        if board is None:
            raise HTTPException(404, "없는 실행입니다")
        return board

    async def relay(call):
        try:
            async with httpx.AsyncClient() as client:
                return await call(client)
        except live.LiveError as exc:
            raise HTTPException(exc.status, exc.detail) from None

    @app.get("/api/pipeline/runs/{run_id}/live/log/{node_id}")
    async def live_log(run_id: str, node_id: str, start: int = 0):
        board = board_of(run_id)
        return await relay(lambda c: live.jenkins_log(c, board, node_id, start))

    @app.get("/api/pipeline/runs/{run_id}/live/sast")
    async def live_sast(run_id: str):
        board = board_of(run_id)
        return await relay(lambda c: live.sast_findings(c, board))

    @app.get("/api/pipeline/runs/{run_id}/live/dast")
    async def live_dast(run_id: str):
        board = board_of(run_id)
        return await relay(lambda c: live.dast_view(c, board, f"/api/pipeline/runs/{run_id}/live/dast"))

    @app.get("/api/pipeline/runs/{run_id}/live/dast/artifacts/{name}")
    async def live_dast_artifact(run_id: str, name: str):
        board = board_of(run_id)
        content = await relay(lambda c: live.dast_artifact(c, board, name))
        return Response(content, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/api/pipeline/runs/{run_id}/live/dast/events")
    async def live_dast_events(run_id: str, request: Request):
        board = board_of(run_id)
        try:
            live.dast_run(board)
        except live.LiveError as exc:
            raise HTTPException(exc.status, exc.detail) from None
        return StreamingResponse(live.dast_events(board, request.headers.get("last-event-id")),
                                 media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return FileResponse(STATIC / "index.html", media_type="text/html")

    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
    return app

