"""프로젝트(점검 대상 시스템) 단위 관리.

프로젝트마다 Git 저장소·운영 서버 주소·점검 대상(스테이징)을 두고, 점검 이력과 취약점 상태를
프로젝트 단위로 묶는다. 운영 서버 주소는 기록용이며 점검은 등록된 스테이징 대상에서만 한다.
"""

import json
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STORE = DATA_DIR / "projects.json"
_lock = threading.Lock()


class ProjectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    repo_url: str = Field(default="", max_length=300)
    prod_url: str = Field(default="", max_length=300)
    target_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    description: str = Field(default="", max_length=300)

    @field_validator("repo_url")
    @classmethod
    def github_repo(cls, value: str) -> str:
        value = value.strip()
        if value and not re.match(r"^(https://github\.com/|git@github\.com:)[\w.-]+/[\w.-]+?(\.git)?/?$", value):
            raise ValueError("GitHub 저장소 주소 형식이어야 합니다.")
        return value

    @field_validator("prod_url")
    @classmethod
    def http_url(cls, value: str) -> str:
        value = value.strip()
        if value and (urlsplit(value).scheme not in {"http", "https"} or not urlsplit(value).hostname):
            raise ValueError("운영 서버 주소는 http(s) URL이어야 합니다.")
        return value.rstrip("/")


def _read() -> list[dict]:
    try:
        return json.loads(STORE.read_text())
    except (OSError, ValueError):
        return []


def _write(items: list[dict]):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp = STORE.with_suffix(".tmp")
    temp.write_text(json.dumps(items, ensure_ascii=False, indent=2))
    temp.replace(STORE)


def ensure_default(target_id: str) -> None:
    """처음 실행하면 예제 프로젝트를 하나 만들어 기존 흐름이 그대로 동작하게 한다."""
    with _lock:
        if not _read():
            _write([{"id": "sample-board", "name": "실습 게시판(예제)", "repo_url": "", "prod_url": "",
                     "target_id": target_id, "description": "로컬 실습 게시판과 sample-target 소스",
                     "created_at": time.time()}])


def list_projects() -> list[dict]:
    return _read()


def get_project(project_id: str) -> dict | None:
    return next((p for p in _read() if p["id"] == project_id), None)


def create_project(data: ProjectInput) -> dict:
    with _lock:
        items = _read()
        project = {"id": uuid4().hex[:12], **data.model_dump(), "created_at": time.time()}
        items.append(project)
        _write(items)
        return project


LIFECYCLE = {
    "REPRODUCED": ("open", "미조치(취약)"),
    "BLOCKED": ("warn", "주의(CSP 차단)"),
    "INCONCLUSIVE": ("check", "확인 필요"),
}


def vulnerability_status(runs: list[dict], fix_state) -> list[dict]:
    """프로젝트의 점검 기록을 시간순으로 훑어 항목별 현재 상태와 이력을 만든다."""
    items: dict[str, dict] = {}
    for run in sorted(runs, key=lambda r: r.get("started_at") or 0):
        for result in run.get("results") or []:
            key = result.get("finding_id")
            if not key:
                continue
            item = items.setdefault(key, {"finding_id": key, "title": result.get("title") or key,
                                          "history": [], "first_seen": run.get("started_at")})
            item["history"].append({"run_id": run["run_id"], "status": result.get("status"),
                                    "at": run.get("started_at"), "reverify": bool(run.get("reverify_of"))})
            item["title"] = result.get("title") or item["title"]
    out = []
    for item in items.values():
        statuses = [h["status"] for h in item["history"]]
        latest = item["history"][-1]
        if latest["status"] == "NOT_REPRODUCED":
            state = ("fixed", "조치 완료(재검증 양호)") if "REPRODUCED" in statuses else ("ok", "양호")
        else:
            state = LIFECYCLE.get(latest["status"], ("check", "확인 필요"))
            earlier = statuses[:-1]
            # 취약 → 양호(조치) 이후 다시 취약이 나오면 재발로 본다.
            if (latest["status"] == "REPRODUCED" and "REPRODUCED" in earlier
                    and "NOT_REPRODUCED" in earlier[earlier.index("REPRODUCED"):]):
                state = ("reopen", "재발")
        fix = None
        for h in reversed(item["history"]):
            fix = fix_state(h["run_id"], item["finding_id"])
            if fix:
                break
        out.append({**item, "latest": latest, "checks": len(statuses), "state": state[0],
                    "state_label": state[1], "fix": fix})
    order = {"open": 0, "reopen": 0, "warn": 1, "check": 2, "fixed": 3, "ok": 4}
    return sorted(out, key=lambda i: (order.get(i["state"], 5), -(i["latest"]["at"] or 0)))
