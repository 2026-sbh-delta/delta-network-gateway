"""SAST 서비스(delta-sast HTTP API) 연동. 작업실 화면에서 SAST → DAST → SAST 피드백을 잇는다.

환경 변수
- DELTA_SAST_URL(기본 http://127.0.0.1:8790), DELTA_SAST_TOKEN: SAST 서버 주소와 Bearer 토큰
- DELTA_SAST_REPO: 점검할 저장소(기본: 시연 게시판 delta-sample-app). 로컬 경로도 된다
- DELTA_SAST_REF: 원격 저장소에서 점검할 브랜치(기본 main). 시작할 때 그 브랜치 끝 커밋으로 고정한다
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import time
import urllib.error
import urllib.request

from .adapters import SARIF_ID_PREFIX, from_sarif, sarif_targets

# DAST 결과 → SAST 피드백 상태. BLOCKED는 필터가 못 막은 것(CSP가 막음)이라 재현으로 본다.
FEEDBACK_STATUS = {"REPRODUCED": "REPRODUCED", "BLOCKED": "REPRODUCED", "NOT_REPRODUCED": "NOT_REPRODUCED"}
VULN_LABEL = {"delta.xss.stored": "저장형 XSS", "delta.xss.reflected": "반사형 XSS", "delta.sqli": "SQL 인젝션",
              "delta.cmdi": "명령 인젝션", "delta.ssti": "템플릿 인젝션", "delta.secret": "하드코딩 비밀값"}
SECRET_LABEL = {"known_token": "알려진 형식의 키", "runtime_secret": "실제 키", "env_default": "환경 변수 기본값",
                "needs_review": "확인 필요", "test_context": "테스트·예시 값", "placeholder": "자리표시 값"}


class SastUnavailable(Exception):
    pass


def settings() -> dict:
    return {"url": os.environ.get("DELTA_SAST_URL", "http://127.0.0.1:8790").rstrip("/"),
            "token": os.environ.get("DELTA_SAST_TOKEN", ""),
            "repo": os.environ.get("DELTA_SAST_REPO", "https://github.com/2026-sbh-delta/delta-sample-app"),
            "ref": os.environ.get("DELTA_SAST_REF", "main")}


def call(method: str, path: str, body: dict | None = None, timeout: float = 20) -> dict:
    cfg = settings()
    if not cfg["token"]:
        raise SastUnavailable("DELTA_SAST_TOKEN이 설정되지 않았다")
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(cfg["url"] + path, data=data, method=method, headers={
        "Authorization": "Bearer " + cfg["token"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 설정한 SAST 주소만
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read()).get("detail", "")
        except (ValueError, AttributeError):
            pass
        raise SastUnavailable(f"SAST 응답 {exc.code}: {str(detail)[:200]}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SastUnavailable("SAST 서버에 연결하지 못했다") from exc


def status() -> dict:
    cfg = settings()
    try:
        call("GET", "/v1/tools", timeout=3)
        reachable = True
    except SastUnavailable:
        reachable = False
    return {"configured": bool(cfg["token"]), "reachable": reachable, "repo": os.path.basename(cfg["repo"])}


def start() -> dict:
    run_id = time.strftime("sast-%Y%m%d-%H%M%S-") + secrets.token_hex(2)
    cfg = settings()
    body = {"run_id": run_id, "repo": cfg["repo"], "mode": "fast"}
    if cfg["repo"].startswith("https://"):
        # SAST는 원격 저장소를 커밋 단위로만 받는다.
        body["commit"] = remote_head(cfg["repo"], cfg["ref"])
    return call("POST", "/v1/sast/scans", body)


def remote_head(repo: str, ref: str) -> str:
    try:
        out = subprocess.run(["git", "ls-remote", repo, f"refs/heads/{ref}"], capture_output=True, text=True,
                             timeout=30, env=os.environ | {"GIT_TERMINAL_PROMPT": "0"}).stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        out = []
    if not out or not re.fullmatch(r"[0-9a-f]{40}", out[0]):
        raise SastUnavailable(f"점검할 저장소의 {ref} 커밋을 확인하지 못했습니다.")
    return out[0]


def _route(value: dict | None) -> str:
    return f"{value['method']} {value['path']}" if value else ""


def rows(sarif: dict) -> list[dict]:
    """화면 표용 요약. 비밀값은 원문 없이 판정·조치만."""
    out = []
    for r in sarif["runs"][0]["results"]:
        props = r.get("properties") or {}
        dast, judgment, secret = props.get("dast") or {}, props.get("judgment") or {}, props.get("secret")
        loc = r["locations"][0]["physicalLocation"]
        row = {"id": r["partialFingerprints"]["deltaFlow/v1"], "rule": r["ruleId"],
               "label": VULN_LABEL.get(r["ruleId"], r["ruleId"]), "kind": r["kind"],
               "where": f"{loc['artifactLocation']['uri']}:{loc['region']['startLine']}",
               "by": judgment.get("by"), "verdict": judgment.get("verdict"), "reason": judgment.get("reason", ""),
               "dast": bool(dast.get("verifiable")) and r["kind"] in ("fail", "review")}
        if secret:
            row |= {"flow": secret.get("name") or "", "context": SECRET_LABEL.get(secret.get("verdict"), ""),
                    "actions": secret.get("actions") or [], "owner": (secret.get("owner") or {}).get("name")}
        else:
            row |= {"flow": f"{dast.get('input')}.{dast.get('field')} · {_route(dast.get('entry'))} → "
                            f"{_route(dast.get('render'))}",
                    "context": dast.get("context", ""), "stored": dast.get("stored", False)}
        out.append(row)
    order = {"fail": 0, "review": 1, "informational": 2, "pass": 3}
    out.sort(key=lambda x: (order.get(x["kind"], 9), x["rule"]))
    return out


def poll(run_id: str) -> dict:
    scan = call("GET", f"/v1/sast/scans/{run_id}")
    result = {"scan": scan}
    if scan.get("state") == "done":
        sarif = call("GET", f"/v1/sast/scans/{run_id}/sarif")
        result["rows"] = rows(sarif)
        result["dast_report"] = from_sarif(sarif) if sarif_targets(sarif) else None
    return result


def feedback(run_id: str, results: list[dict]) -> list[dict] | None:
    """DAST 결과를 SAST로 되돌린다. sast-<경로 ID> 항목만."""
    items = []
    for r in results:
        fid = str(r.get("finding_id", ""))
        if fid.startswith(SARIF_ID_PREFIX):
            items.append({"fingerprint": fid[len(SARIF_ID_PREFIX):],
                          "status": FEEDBACK_STATUS.get(r.get("status"), "ERROR")})
    if not items:
        return None
    return call("POST", f"/v1/sast/scans/{run_id}/dast-results", {"results": items})
