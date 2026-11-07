"""관리자 화면의 '지금 무엇을 하는지' 보기. 각 도구가 내놓는 진행 정보를 플랫폼 내부망에서 받아 그대로 넘긴다.

어느 빌드·점검을 볼지는 요청이 정하지 않는다. 실행 기록(Board)에 남은 그 실행의 Jenkins 빌드 번호·SAST·DAST 점검 ID 만 쓴다.
그래서 이 화면으로 다른 실행이나 임의 주소를 들여다볼 수 없다.

- Jenkins: 빌드·스테이징 배포 콘솔 로그(progressiveText, 이어 읽기)
- SAST: SARIF 에서 탐지 항목과 입력→출력 경로
- DAST: 점검 상태·판정·증거 스크린샷, 실시간 브라우저 화면·에이전트 행동(SSE 중계)
"""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx

JOBS = {"build": "delta-build", "stage": "delta-deploy"}
ARTIFACT = re.compile(r"[A-Za-z0-9_-]{1,90}\.png")
LOG_CHUNK = 256 * 1024  # 한 번에 넘기는 콘솔 로그 최대 크기
SARIF_CACHE: dict[str, list[dict[str, Any]]] = {}
# Jenkins 콘솔의 숨은 주석(ESC[8mha:…ESC[0m)과 색 코드는 화면에 그대로 보이면 읽기 어렵다
CONSOLE_NOTE = re.compile(r"\x1b\[8mha:[^\x1b]*\x1b\[0m")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class LiveError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


def _zone() -> str:
    return os.environ.get("DELTA_INTERNAL_ZONE", "cp.internal")


def _node(board: dict[str, Any], node_id: str) -> dict[str, Any]:
    return next((n for n in board.get("nodes", []) if n["id"] == node_id), None) or {}


def jenkins_build(board: dict[str, Any], node_id: str) -> tuple[str, int]:
    if board.get('options',{}).get('execution_cloud')=='onprem':
        raise LiveError(404,'온프레미스 등록 이미지는 별도 Jenkins 콘솔 번호를 사용하지 않습니다')
    if node_id not in JOBS:
        raise LiveError(404, "콘솔 로그가 없는 단계입니다")
    build = (_node(board, node_id).get("detail") or {}).get("build")
    if not isinstance(build, int) or build < 1:
        raise LiveError(404, "아직 Jenkins 빌드가 시작되지 않았습니다")
    return JOBS[node_id], build


def dast_run(board: dict[str, Any]) -> str:
    run_id = (_node(board, "dast").get("detail") or {}).get("run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f-]{36}", run_id):
        raise LiveError(404, "아직 DAST 점검이 시작되지 않았습니다")
    return run_id


def sast_run(board: dict[str, Any]) -> str:
    run_id = (_node(board, "sast").get("detail") or {}).get("run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", run_id):
        raise LiveError(404, "아직 SAST 점검이 시작되지 않았습니다")
    return run_id


async def jenkins_log(client: httpx.AsyncClient, board: dict[str, Any], node_id: str, start: int) -> dict[str, Any]:
    job, build = jenkins_build(board, node_id)
    password = os.environ.get("JENKINS_PLATFORM_PASSWORD")
    if not password:
        raise LiveError(503, "Jenkins 계정이 설정되지 않았습니다")
    base = f"http://jenkins.{_zone()}:8080/job/{job}/{build}"
    try:
        r = await client.get(f"{base}/logText/progressiveText", params={"start": max(0, start)},
                             auth=("platform", password), timeout=10)
    except httpx.HTTPError as exc:
        raise LiveError(502, f"Jenkins 에 닿지 못했습니다({type(exc).__name__})") from None
    if r.status_code == 404:
        raise LiveError(404, "Jenkins 빌드 기록이 없습니다(인프라를 다시 만들었으면 지워졌을 수 있습니다)")
    if r.status_code != 200:
        raise LiveError(502, f"Jenkins 응답 {r.status_code}")
    text = ANSI.sub("", CONSOLE_NOTE.sub("", r.text)).replace("\r\n", "\n").replace("\r", "")
    following = int(r.headers.get("X-Text-Size", start + len(r.content)))
    if len(text) > LOG_CHUNK:  # 처음 열 때 긴 로그는 끝부분만 보낸다
        text = "…(앞부분 생략)\n" + text[-LOG_CHUNK:]
    return {"job": job, "build": build, "text": text, "next": following,
            "more": r.headers.get("X-More-Data") == "true"}


def summarize_sarif(sarif: dict[str, Any]) -> list[dict[str, Any]]:
    """SARIF 결과를 화면용으로 줄인다. 값(비밀값 미리보기 등)은 SAST 가 이미 가린 문구만 쓴다."""
    def where(location: dict[str, Any]) -> dict[str, Any]:
        physical = location.get("physicalLocation") or {}
        return {"file": (physical.get("artifactLocation") or {}).get("uri", ""),
                "line": (physical.get("region") or {}).get("startLine")}

    out = []
    for run in sarif.get("runs") or []:
        for result in run.get("results") or []:
            props = result.get("properties") or {}
            dast, judgment = props.get("dast") or {}, props.get("judgment") or {}
            flows = (((result.get("codeFlows") or [{}])[0].get("threadFlows") or [{}])[0].get("locations")) or []
            steps = [where(step.get("location") or {}) | {"note": ((step.get("location") or {}).get("message") or {}).get("text", "")}
                     for step in flows]
            fingerprint = (result.get("partialFingerprints") or {}).get("deltaFlow/v1", "")
            out.append({
                "id": "sast-" + fingerprint if fingerprint else "",
                "rule": result.get("ruleId", ""), "kind": result.get("kind", ""), "level": result.get("level", ""),
                "message": (result.get("message") or {}).get("text", "")[:400],
                "sink": where((result.get("locations") or [{}])[0]),
                "steps": steps[:12],
                "dast": bool(dast.get("verifiable")),
                "entry": dast.get("entry"), "stored": dast.get("stored"),
                "judgment": {k: judgment.get(k) for k in ("by", "verdict", "reason") if judgment.get(k)},
            })
    order = {"fail": 0, "review": 1, "open": 2, "pass": 3}
    return sorted(out, key=lambda f: (order.get(f["kind"], 4), not f["dast"]))


async def sast_findings(client: httpx.AsyncClient, board: dict[str, Any]) -> dict[str, Any]:
    run_id = sast_run(board)
    if run_id not in SARIF_CACHE:
        try:
            r = await client.get(f"http://sast.{_zone()}:8790/v1/sast/scans/{run_id}/sarif", timeout=15,
                                 headers={"Authorization": f"Bearer {os.environ.get('DELTA_SAST_TOKEN', '')}"})
        except httpx.HTTPError as exc:
            raise LiveError(502, f"SAST 에 닿지 못했습니다({type(exc).__name__})") from None
        if r.status_code in (404, 409):
            raise LiveError(404, "SAST 결과가 아직 없습니다")
        if r.status_code != 200:
            raise LiveError(502, f"SAST 응답 {r.status_code}")
        findings = summarize_sarif(r.json())
        if len(SARIF_CACHE) > 64:
            SARIF_CACHE.clear()
        SARIF_CACHE[run_id] = findings  # 끝난 점검의 SARIF 는 바뀌지 않는다
    return {"run_id": run_id, "findings": SARIF_CACHE[run_id]}


def _dast_base() -> str:
    # Host 가 DAST_INTERNAL_HOSTS(dast.cp.internal:8765)와 맞아야 내부 조회(GET)가 통한다
    return f"http://dast.{_zone()}:8765"


def _relink(value: Any, rewrite: Callable[[str], str]) -> Any:
    if isinstance(value, dict):
        return {k: _relink(v, rewrite) for k, v in value.items()}
    if isinstance(value, list):
        return [_relink(v, rewrite) for v in value]
    if isinstance(value, str) and value.startswith("/api/artifacts/"):
        return rewrite(value)
    return value


async def dast_view(client: httpx.AsyncClient, board: dict[str, Any], prefix: str) -> dict[str, Any]:
    run_id = dast_run(board)
    try:
        r = await client.get(f"{_dast_base()}/api/runs/{run_id}/view", timeout=10)
    except httpx.HTTPError as exc:
        raise LiveError(502, f"DAST 에 닿지 못했습니다({type(exc).__name__})") from None
    if r.status_code == 404:
        raise LiveError(404, "DAST 점검 기록이 없습니다")
    if r.status_code != 200:
        raise LiveError(502, f"DAST 응답 {r.status_code}")
    data = r.json()
    keep = {k: data.get(k) for k in ("run_id", "status", "live", "started_at", "duration_ms", "counts", "error", "plan",
                                      "sast_feedback")}
    keep["results"] = data.get("results") or []
    keep["findings"] = [{"id": f.get("id"), "title": f.get("title")} for f in ((data.get("report") or {}).get("findings") or [])]
    # 증거 스크린샷 주소를 이 화면의 중계 주소로 바꾼다(DAST 는 공개 로그인 없이 내부에서만 읽는다)
    return _relink(keep, lambda path: prefix + "/artifacts/" + path.rsplit("/", 1)[-1])


async def dast_fix(client: httpx.AsyncClient, board: dict[str, Any]) -> dict[str, Any]:
    """재현된 항목에 DAST 가 만든 수정안(패치·바뀐 파일·코드 흐름). 승인 화면에서 수정 전·후 코드를 보여 준다(읽기만)."""
    run_id = dast_run(board)
    results = (_node(board, "dast").get("detail") or {}).get("results") or []
    finding = next((r.get("finding_id") for r in results if r.get("status") in ("REPRODUCED", "BLOCKED")), None)
    if not isinstance(finding, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", finding):
        raise LiveError(404, "수정안을 만들 재현 항목이 없습니다")
    token = os.environ.get("DAST_PLATFORM_TOKEN", "")
    try:
        r = await client.get(f"{_dast_base()}/api/runs/{run_id}/findings/{finding}/fix",
                             headers={"Authorization": f"Bearer {token}"}, timeout=10)
    except httpx.HTTPError as exc:
        raise LiveError(502, f"DAST 에 닿지 못했습니다({type(exc).__name__})") from None
    if r.status_code != 200:
        raise LiveError(502, f"DAST 응답 {r.status_code}")
    data = r.json()
    return {"finding_id": finding, **{k: data.get(k) for k in ("status", "summary", "changes", "files", "patch", "trace")}}


async def dast_artifact(client: httpx.AsyncClient, board: dict[str, Any], name: str) -> bytes:
    run_id = dast_run(board)
    if not ARTIFACT.fullmatch(name):
        raise LiveError(404, "허용되지 않은 파일입니다")
    try:
        r = await client.get(f"{_dast_base()}/api/artifacts/{run_id}/{name}", timeout=15)
    except httpx.HTTPError as exc:
        raise LiveError(502, f"DAST 에 닿지 못했습니다({type(exc).__name__})") from None
    if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/png"):
        raise LiveError(404, "스크린샷이 없습니다")
    return r.content


async def dast_events(board: dict[str, Any], last_event_id: str | None) -> AsyncIterator[bytes]:
    """DAST 의 SSE(화면 프레임·단계·에이전트 행동·결과)를 그대로 넘긴다. 점검이 끝나면 DAST 가 스트림을 닫는다."""
    run_id = dast_run(board)
    headers = {"Accept": "text/event-stream"}
    if last_event_id and last_event_id.isdigit():
        headers["Last-Event-ID"] = last_event_id
    timeout = httpx.Timeout(10, read=60)  # DAST 는 10초마다 연결 유지 줄을 보낸다
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            async with client.stream("GET", f"{_dast_base()}/api/runs/{run_id}/events", headers=headers) as r:
                if r.status_code != 200:
                    yield f"event: gone\ndata: {r.status_code}\n\n".encode()
                    return
                async for chunk in r.aiter_raw():
                    yield chunk
        except httpx.HTTPError:
            yield b"event: gone\ndata: 502\n\n"
