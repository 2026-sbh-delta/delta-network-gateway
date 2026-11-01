"""localhost 제어 API, 단일 실행, SSE 및 검증 아티팩트 제공."""

import asyncio
from collections import deque
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from .adapters import normalize_report
from .contracts import MAX_REPORT_BYTES, ROOT, SCENARIOS, Report, RunRequest, sample_report
from .agent import investigate
from .orchestrator import orchestrate
from .chat import ChatStore, ask as ask_chat, chat_context
from .fixer import create_pr, propose_fix, repo_source
from . import projects
from .report import build as build_report
from .computer_use import investigate as investigate_computer_use
from .planner import DEFAULT_MODEL, plan
from .registry import load_catalog
from .runner import browser_available, verify


def _claude_auth() -> dict:
    """키 값이나 계정 식별자를 노출하지 않고 Claude Code 인증 상태만 읽는다."""
    try:
        completed = subprocess.run(
            ["claude", "auth", "status", "--json"], capture_output=True, text=True,
            timeout=3, check=False,
        )
        value = json.loads(completed.stdout) if completed.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        value = {}
    return {
        "cli_available": bool(value) or completed.returncode == 0 if 'completed' in locals() else False,
        "logged_in": bool(value.get("loggedIn") or value.get("logged_in")),
        "method": str(value.get("authMethod") or value.get("auth_method") or "none")[:40],
    }


def _codex_auth() -> dict:
    """Codex CLI 출력에서 로그인 방식만 판별하고 원문은 반환하지 않는다."""
    try:
        completed = subprocess.run(
            ["codex", "login", "status"], capture_output=True, text=True,
            timeout=3, check=False,
        )
        output = (completed.stdout + completed.stderr).lower()
    except (OSError, subprocess.SubprocessError):
        return {"cli_available": False, "logged_in": False, "method": "none"}
    method = "chatgpt" if "chatgpt" in output else "api-key" if "api key" in output else "unknown"
    return {"cli_available": True, "logged_in": completed.returncode == 0, "method": method}


MAX_PARALLEL = 3


def create_app(port: int = 8765, target_port: int = 8766,
               runs_dir: Path | None = None, on_run_start=None) -> FastAPI:
    if port == target_port or not (1024 <= port <= 65535 and 1024 <= target_port <= 65535):
        raise ValueError("제어 화면과 대상은 서로 다른 1024~65535 포트를 사용해야 합니다.")
    output_root = (runs_dir or ROOT / "runs").resolve()
    static_root = (Path(__file__).parent / "static").resolve()
    catalog = load_catalog()
    DEFAULT_PROJECT = "sample-board"
    projects.ensure_default(sorted(catalog.targets)[0])
    local_route, _ = catalog.resolve(
        "local-fixture", "board-post", "body", "visual_svg_v1", "vulnerable", target_port,
    )
    if output_root == static_root or static_root in output_root.parents:
        raise ValueError("아티팩트는 static 외부에 저장해야 합니다.")
    token = secrets.token_urlsafe(32)
    runs: dict[str, dict] = {}
    tasks: set[asyncio.Task] = set()
    model_runtime = {
        "claude_agent": {"state": "not_tested", "checked_at": None, "detail": "실호출 전"},
        "claude_computer": {"state": "not_tested", "checked_at": None, "detail": "실호출 전"},
        "codex_computer": {"state": "not_tested", "checked_at": None, "detail": "실호출 전"},
    }

    def mark_model(model_id: str, state: str, detail: str):
        model_runtime[model_id] = {
            "state": state,
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "detail": detail[:120],
        }

    def model_statuses() -> list[dict]:
        claude = _claude_auth()
        codex = _codex_auth()
        anthropic_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        anthropic_token = bool(os.environ.get("ANTHROPIC_AUTH_TOKEN"))
        openai_key = bool(os.environ.get("OPENAI_API_KEY"))
        openai_oauth = bool(os.environ.get("OPENAI_ACCESS_TOKEN"))
        claude_model = os.environ.get("DAST_CLAUDE_MODEL", DEFAULT_MODEL)
        codex_model = os.environ.get("DAST_CODEX_MODEL", "gpt-6.1-sol")
        return [
            {
                "id": "claude_agent", "provider": "Anthropic", "label": "Claude Agent SDK",
                "model": claude_model, "key_present": anthropic_key,
                "login_present": claude["logged_in"], "login_method": claude["method"],
                "login_scope": "Claude Code 세션",
                "credential_present": anthropic_key or anthropic_token or claude["logged_in"],
                **model_runtime["claude_agent"],
            },
            {
                "id": "claude_computer", "provider": "Anthropic", "label": "Claude Computer Use",
                "model": claude_model, "key_present": anthropic_key,
                "login_present": claude["logged_in"], "login_method": claude["method"],
                "login_scope": "Claude Code 세션 · Computer Use 인증 아님",
                "credential_present": anthropic_key or anthropic_token,
                **model_runtime["claude_computer"],
            },
            {
                "id": "codex_computer", "provider": "OpenAI", "label": "Codex Computer Use",
                "model": codex_model, "key_present": openai_key,
                "login_present": codex["logged_in"], "login_method": codex["method"],
                "login_scope": "Codex CLI 세션 · Computer Use OAuth 아님",
                "oauth_present": openai_oauth,
                "credential_present": openai_key or openai_oauth,
                **model_runtime["codex_computer"],
            },
        ]

    @asynccontextmanager
    async def lifespan(_app):
        yield
        for task in tuple(tasks):
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.runs = runs

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.headers.get("host", "")
        origin = request.headers.get("origin")
        if (host not in {f"127.0.0.1:{port}", f"localhost:{port}"}
                or (origin is not None and origin != f"http://{host}")
                or request.headers.get("sec-fetch-site") == "cross-site"):
            return JSONResponse({"detail": "허용되지 않은 요청 출처입니다."}, status_code=403)
        if request.method not in {"GET", "HEAD"} and not secrets.compare_digest(
            request.headers.get("x-dast-token", ""), token,
        ):
            return JSONResponse({"detail": "제어 토큰이 유효하지 않습니다."}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        return response

    def summary(run):
        result = {key: run[key] for key in (
            "run_id", "status", "scenario", "planner", "results", "duration_ms",
        )}
        result["concurrency"] = run.get("concurrency", 1)
        result["project_id"] = run.get("project_id") or DEFAULT_PROJECT
        if run.get("reverify_of"):
            result["reverify_of"] = run["reverify_of"]
        result.update(source=run.get("source", "unvalidated"),
                      source_label=run.get("source_label", "검증되지 않은 입력"))
        if run.get("plan"):
            result["plan"] = run["plan"]
        if run.get("error"):
            result["error"] = run["error"]
        if run["status"] != "running":
            result["report_url"] = f'/api/artifacts/{run["run_id"]}/report.json'
        result.update(started_at=run.get("started_at"), **tally(run.get("report"), run["results"]))
        return result

    def tally(report, results):
        findings = report.get("findings") if isinstance(report, dict) else None
        counts = {}
        for item in results or []:
            counts[item.get("status", "INCONCLUSIVE")] = counts.get(item.get("status", "INCONCLUSIVE"), 0) + 1
        return {"finding_count": len(findings) if isinstance(findings, list) else len(results or []),
                "counts": counts}

    def disk_run(run_id: str) -> dict | None:
        """서버 재시작 전 점검도 보이도록 runs/<id>/report.json에서 기록을 읽는다."""
        path = output_root / run_id / "report.json"
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        data.setdefault("started_at", path.stat().st_mtime - (data.get("duration_ms") or 0) / 1000)
        data["run_id"] = run_id
        data.setdefault("project_id", None)
        data["project_id"] = data["project_id"] or DEFAULT_PROJECT
        return data | tally(data.get("input"), data.get("results"))

    async def emit(run, event):
        run["sequence"] += 1
        if event.get("type") == "frame":
            # 화면 프레임은 항목별 최신 한 장만 둔다. 새로고침 재생 버퍼에서 결과 이벤트가 밀려나지 않게 한다.
            run.setdefault("frames", {})[event.get("finding_id", "")] = (run["sequence"], event)
        else:
            run["events"].append((run["sequence"], event))
        # 연결된 모든 스트림을 깨우도록 이벤트 객체를 교체해 알린다.
        changed, run["changed"] = run["changed"], asyncio.Event()
        changed.set()

    async def run_agent(run, report, output, publish):
        default_target = sorted(catalog.targets)[0]
        limit = asyncio.Semaphore(run.get("concurrency", 1))

        async def check(finding):
            # 이 취약점의 화면·커서·로그 이벤트에 항목 ID를 붙여 UI가 항목별로 나눠 보여주게 한다.
            async def tagged(event):
                await publish(event if "finding_id" in event else {**event, "finding_id": finding.id})

            hint = finding.trigger.model_dump() if finding.trigger else {}
            target_id = hint.get("target_id") or default_target
            payload_id = hint.get("payload_id") or (
                "visual_svg_v1" if "visual_svg_v1" in catalog.payloads else sorted(catalog.payloads)[0])
            mapping = {"target_id": target_id, "payload_id": payload_id, "planner_mode": run["planner"],
                       "model_name": os.environ.get("DAST_CLAUDE_MODEL", DEFAULT_MODEL),
                       "reason": "AI 에이전트가 화면을 보고 입력 위치를 찾습니다."}
            async with limit:
                await tagged({"type": "finding", "status": "running",
                              "message": mapping["reason"], "mapping": mapping})
                try:
                    target = catalog.resolve_target(target_id, run["scenario"], target_port)
                    if payload_id not in catalog.payloads:
                        raise ValueError("등록되지 않은 payload입니다.")
                    template = catalog.payloads[payload_id]
                    if finding.payload is not None and finding.payload != template:
                        raise ValueError("원문 payload가 등록 템플릿과 일치하지 않습니다.")
                except ValueError as exc:
                    result = {"finding_id": finding.id, "title": finding.title,
                              "status": "INCONCLUSIVE", "message": str(exc),
                              "evidence": {"browser_started": False}, "duration_ms": 0}
                else:
                    result = await orchestrate(finding, target, payload_id, template,
                                               run["run_id"], output, tagged, run["planner"],
                                               run.get("impact", True),
                                               resume=run.get("resume", {}).get(finding.id))
            result["mapping"] = mapping
            run["results"].append(result)
            await tagged({"type": "finding", "status": result["status"],
                          "message": result["message"], "mapping": mapping})
            await tagged({"type": "result", "result": result})
            # 항목 점검이 끝나는 즉시 수정안(코드 흐름 포함)을 만든다. 다른 항목 점검과 겹쳐 돈다.
            if run.get("auto_fix", True) and result["status"] in {"REPRODUCED", "BLOCKED"}:
                try:
                    record = record_of(run["run_id"], finished=False)
                    launch_fix(run["run_id"], finding.id, record, fix_target(record, finding.id))
                except HTTPException:
                    pass

        await asyncio.gather(*(check(finding) for finding in report.findings))
        order = {finding.id: index for index, finding in enumerate(report.findings)}
        run["results"].sort(key=lambda item: order.get(item["finding_id"], 0))
        run["status"] = "done"
        if any(item["status"] == "INCONCLUSIVE" for item in run["results"]):
            run["status"] = "error"
            run["error"] = "하나 이상의 항목에서 검증을 완료하지 못했습니다."

    async def execute(run, raw_report):
        start = time.monotonic()
        output = output_root / run["run_id"]
        phase = "validate"

        async def publish(event):
            await emit(run, event)

        try:
            output.mkdir(parents=True, exist_ok=False)
            await publish({"type": "state", "phase": phase,
                           "message": "SAST 입력과 등록된 payload·경로를 확인합니다."})
            report = Report.model_validate(normalize_report(raw_report))
            run["source"] = report.source
            run["source_label"] = report.source_label
            await publish({"type": "log", "message": "SAST 입력 출처: " + report.source_label})
            if run["planner"] in {"agent", "computer_use"}:
                phase = "browser"
                await run_agent(run, report, output, publish)
                model_id = "claude_computer" if run["planner"] == "computer_use" else "claude_agent"
                evidence = [item.get("evidence", {}) for item in run["results"]]
                called = any(
                    item.get("num_turns") is not None
                    or sum((item.get("usage") or {}).values()) > 0
                    for item in evidence
                )
                mark_model(model_id, "available" if called else "failed",
                           "실호출 성공" if called else "호출 완료 증거 없음")
                return
            phase = "plan"
            await publish({"type": "state", "phase": phase, "message":
                           "수동 검토된 SAST 계획을 사용합니다." if run["planner"] == "reviewed"
                           else "Claude SDK가 제한된 검증 계획을 작성합니다."})
            decisions = await plan(report, run["planner"], catalog)
            run["plan"] = decisions.model_dump()
            if decisions.model_name not in {"none", "not-called"}:
                mark_model("claude_agent", "available", "실호출 성공")
            await publish({"type": "log", "message": (
                "계획 모델: 사용 안 함" if decisions.model_name == "none"
                else "계획 모델: " + decisions.model_name
            )})
            findings = {finding.id: finding for finding in report.findings}
            phase = "browser"
            for decision in decisions.decisions:
                finding = findings[decision.finding_id]
                mapping = {
                    "target_id": decision.target_id,
                    "route_id": decision.route_id,
                    "field": decision.field,
                    "payload_id": decision.payload_id,
                    "reason": decision.reason,
                    "model_name": decisions.model_name,
                    "planner_mode": run["planner"],
                }
                await publish({"type": "finding", "finding_id": finding.id,
                               "status": "running", "message": decision.reason,
                               "mapping": mapping})
                if decision.decision == "insufficient":
                    result = {"finding_id": finding.id, "title": finding.title,
                              "status": "INCONCLUSIVE", "message": decision.reason,
                              "evidence": {"browser_started": False}, "duration_ms": 0}
                else:
                    try:
                        route, payload_template = catalog.resolve(
                            decision.target_id, decision.route_id, decision.field,
                            decision.payload_id, run["scenario"], target_port,
                        )
                        hint = finding.trigger.model_dump() if finding.trigger else {}
                        if any(value is not None and value != getattr(decision, key)
                               for key, value in hint.items()):
                            raise ValueError("모델 선택이 입력 힌트와 일치하지 않습니다.")
                        if finding.payload is not None and finding.payload != payload_template:
                            raise ValueError("원문 payload가 등록 템플릿과 일치하지 않습니다.")
                    except ValueError as exc:
                        result = {"finding_id": finding.id, "title": finding.title,
                                  "status": "INCONCLUSIVE", "message": str(exc),
                                  "evidence": {"browser_started": False}, "duration_ms": 0}
                    else:
                        result = await verify(
                            finding, route, decision.payload_id, payload_template,
                            run["run_id"], output, publish,
                        )
                result["mapping"] = mapping
                run["results"].append(result)
                await publish({"type": "finding", "finding_id": finding.id,
                               "status": result["status"], "message": result["message"],
                               "mapping": mapping})
                await publish({"type": "result", "result": result})
            run["status"] = "done"
            if any(item["status"] == "INCONCLUSIVE" for item in run["results"]):
                run["status"] = "error"
                run["error"] = "하나 이상의 항목에서 검증을 완료하지 못했습니다."
        except Exception as exc:
            run["status"] = "error"
            message = {
                "validate": "입력이 등록된 SAST·payload·경로 계약과 일치하지 않습니다.",
                "plan": "계획 생성에 실패했습니다. 다른 planner로 자동 대체하지 않습니다.",
                "browser": "브라우저 검증을 완료하지 못했습니다.",
            }[phase]
            run["error"] = message
            completed = {item["finding_id"] for item in run["results"]}
            # Never reflect unvalidated attacker-controlled strings in error events.
            candidates = raw_report.get("findings", []) if isinstance(raw_report, dict) else []
            ids = []
            if isinstance(candidates, list):
                for item in candidates[:10]:
                    if not isinstance(item, dict):
                        continue
                    candidate = item.get("id") or item.get("findingId")
                    if isinstance(candidate, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", candidate):
                        ids.append(candidate)
            for finding_id in dict.fromkeys(ids or ["REPORT"]):
                if finding_id in completed:
                    continue
                result = {"finding_id": finding_id, "status": "INCONCLUSIVE",
                          "message": message, "duration_ms": 0,
                          "evidence": {"phase": phase, "error_type": type(exc).__name__}}
                run["results"].append(result)
                await publish({"type": "finding", "finding_id": finding_id,
                               "status": "INCONCLUSIVE", "message": message})
                await publish({"type": "result", "result": result})
            await publish({"type": "state", "phase": "error", "message": message})
        finally:
            run["duration_ms"] = round((time.monotonic() - start) * 1000)
            if run["status"] == "running":
                run["status"] = "error"
                run["error"] = "실행이 중단되었습니다."
            try:
                artifact = summary(run) | {"trace": "dast-mvp-20261101", "input": run["report"],
                                           "scope": "registry에 명시된 target·route·payload만 지원"}
                (output / "report.json").write_text(json.dumps(artifact, ensure_ascii=False, indent=2))
            except OSError:
                run["status"] = "error"
                run["error"] = "실행 증거 파일을 저장하지 못했습니다."
            await publish({"type": "done", **summary(run)})
            if run["planner"] in {"agent", "computer_use"} and (output / "report.json").is_file():
                schedule_report(run["run_id"], auto_fix=run.get("auto_fix", True))

    report_jobs: dict[str, str] = {}

    def schedule_report(run_id: str, auto_fix: bool = False):
        # 점검이 끝나면 취약 항목의 수정안을 먼저 만들고(선택), 최종 보고서(HTML·PDF)를 만든다.
        async def job():
            report_jobs[run_id] = "generating"
            if auto_fix:
                await auto_fixes(run_id)
            try:
                directory = output_root / run_id
                record = json.loads((directory / "report.json").read_text()) | {"run_id": run_id}
                await build_report(record, directory)
                report_jobs[run_id] = "ready"
            except Exception:
                report_jobs[run_id] = "failed"
        task = asyncio.create_task(job())
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    async def auto_fixes(run_id: str):
        try:
            record = record_of(run_id)
        except HTTPException:
            return
        for result in (record or {}).get("results", []):
            finding_id = result.get("finding_id")
            if result.get("status") not in {"REPRODUCED", "BLOCKED"}:
                continue
            # 점검 중에 항목별로 이미 시작한 수정안은 기다리기만 한다.
            task = fix_tasks.get((run_id, finding_id))
            if task is None and not (output_root / run_id / "fix" / finding_id / "fix.json").is_file():
                try:
                    task = launch_fix(run_id, finding_id, record, fix_target(record, finding_id))
                except HTTPException:
                    continue
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)

    def report_state(run_id: str) -> dict:
        directory = output_root / run_id
        ready = (directory / "report.html").is_file() and (directory / "report.pdf").is_file()
        status = report_jobs.get(run_id) or ("ready" if ready else "none")
        if status == "generating" or not ready:
            return {"status": status}
        base = f"/api/artifacts/{run_id}"
        return {"status": "ready", "html": base + "/report.html", "pdf": base + "/report.pdf"}

    @app.get("/api/runs/{run_id}/report")
    async def report_status(run_id: str):
        try:
            if str(UUID(run_id)) != run_id:
                raise ValueError
        except ValueError:
            raise HTTPException(404, "실행 기록을 찾을 수 없습니다.") from None
        return report_state(run_id)

    @app.post("/api/runs/{run_id}/report")
    async def regenerate_report(run_id: str):
        try:
            if str(UUID(run_id)) != run_id:
                raise ValueError
        except ValueError:
            raise HTTPException(404, "실행 기록을 찾을 수 없습니다.") from None
        if not (output_root / run_id / "report.json").is_file():
            raise HTTPException(404, "완료된 점검 기록이 없습니다.")
        if report_jobs.get(run_id) != "generating":
            schedule_report(run_id)
        return {"status": "generating"}

    @app.get("/api/config")
    async def config():
        try:
            sample = sample_report()
        except (OSError, ValueError):
            sample = {}
        try:
            multi = json.loads((ROOT / "examples" / "sast-multi.json").read_text())
        except (OSError, ValueError):
            multi = None
        return {"csrf_token": token, "scenarios": SCENARIOS, "default_planner": "agent",
                "sample_report": sample, "multi_sample_report": multi,
                "browser_available": await browser_available(),
                "target_origin": local_route.origin,
                "target_ids": sorted(catalog.targets),
                "payload_ids": sorted(catalog.payloads),
                "claude_model": os.environ.get("DAST_CLAUDE_MODEL", DEFAULT_MODEL),
                "models": await asyncio.to_thread(model_statuses)}

    @app.get("/api/models")
    async def models():
        return {"models": await asyncio.to_thread(model_statuses)}

    @app.post("/api/runs")
    async def start_run(request: Request):
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            raise HTTPException(415, "JSON 요청만 허용합니다.")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_REPORT_BYTES:
                raise HTTPException(413, "입력은 최대 128KB입니다.")
        try:
            args = RunRequest.model_validate_json(body)
        except (ValidationError, ValueError):
            raise HTTPException(422, "scenario·planner·report 요청 형식을 확인하세요.") from None
        return {"run_id": launch(args)}

    def launch(args: RunRequest, extra: dict | None = None) -> str:
        project_id = args.project_id or (extra or {}).get("project_id") or DEFAULT_PROJECT
        if not projects.get_project(project_id):
            raise HTTPException(422, "등록되지 않은 프로젝트입니다.")
        running = [run for run in runs.values() if run["status"] == "running"]
        if len(running) >= MAX_PARALLEL:
            raise HTTPException(429, f"동시에 {MAX_PARALLEL}개까지 점검할 수 있습니다. 진행 중인 점검이 끝난 뒤 다시 요청하세요.")
        while len(runs) >= 100:
            finished = next((key for key, value in runs.items() if value["status"] != "running"), None)
            if finished is None:
                break
            runs.pop(finished)
        run_id = str(uuid4())
        run = {"run_id": run_id, "status": "running", "scenario": args.scenario,
               "planner": args.planner, "concurrency": args.concurrency, "impact": args.impact,
               "auto_fix": args.auto_fix, "results": [], "duration_ms": 0,
               "events": deque(maxlen=2000), "sequence": 0, "changed": asyncio.Event(),
               "report": args.report, "started_at": time.time(), **(extra or {}), "project_id": project_id}
        runs[run_id] = run
        if on_run_start is not None and not running:
            # 로컬 실습 대상은 매 점검을 같은 초기 상태에서 시작한다.
            # 병렬 점검 중에는 다른 점검의 글이 사라지지 않도록 초기화하지 않는다.
            on_run_start()
        task = asyncio.create_task(execute(run, args.report))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return run_id

    fix_jobs: dict[tuple[str, str], dict] = {}

    def valid_ids(run_id: str, finding_id: str):
        try:
            if str(UUID(run_id)) != run_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", finding_id):
                raise ValueError
        except ValueError:
            raise HTTPException(404, "실행 기록을 찾을 수 없습니다.") from None

    def record_of(run_id: str, finished: bool = True) -> dict | None:
        # finished=False면 진행 중인 점검도 지금까지 끝난 항목 기준으로 돌려준다.
        source = runs.get(run_id)
        if source:
            if finished and source["status"] == "running":
                raise HTTPException(409, "점검이 끝난 뒤에 요청하세요.")
            return {"report": source["report"], "results": list(source["results"]),
                    "scenario": source["scenario"], "impact": source.get("impact", True),
                    "project_id": source.get("project_id")}
        return disk_run(run_id)

    def repo_of(record: dict, finding_id: str):
        """점검이 끝난 항목의 대상 저장소. 판정 결과와 무관하게 코드 확인에 쓴다."""
        result = next((r for r in record.get("results", []) if r.get("finding_id") == finding_id), None)
        if not result:
            raise HTTPException(409, "이 항목의 점검이 아직 끝나지 않았습니다.")
        target_id = ((result.get("evidence") or {}).get("target_id")
                     or (result.get("mapping") or {}).get("target_id", ""))
        target = catalog.targets.get(target_id)
        if not target or not target.repo:
            raise HTTPException(409, "대상에 수정할 저장소(repo)가 설정되지 않았습니다.")
        return target.repo

    def fix_target(record: dict, finding_id: str):
        result = next((r for r in record.get("results", []) if r.get("finding_id") == finding_id), None)
        if not result or result.get("status") not in {"REPRODUCED", "BLOCKED"}:
            raise HTTPException(409, "취약 또는 주의로 판정된 항목만 수정안을 만들 수 있습니다.")
        return repo_of(record, finding_id)

    fix_tasks: dict[tuple[str, str], asyncio.Task] = {}

    def launch_fix(run_id: str, finding_id: str, record: dict, repo) -> asyncio.Task:
        """항목 하나의 수정안(코드 흐름 포함)을 백그라운드로 만든다. 이미 돌고 있으면 그 작업을 돌려준다."""
        key = (run_id, finding_id)
        if key in fix_tasks and not fix_tasks[key].done():
            return fix_tasks[key]
        directory = output_root / run_id / "fix" / finding_id
        directory.mkdir(parents=True, exist_ok=True)
        fix_jobs[key] = {"status": "generating"}

        async def job():
            try:
                await propose_fix(record, finding_id, repo, directory)
                fix_jobs[key] = {"status": "ready"}
            except Exception as exc:
                fix_jobs[key] = {"status": "failed",
                                 "error": str(exc) if type(exc) is RuntimeError else type(exc).__name__}
        task = asyncio.create_task(job())
        fix_tasks[key] = task
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return task

    def fix_state(run_id: str, finding_id: str) -> dict:
        directory = output_root / run_id / "fix" / finding_id
        job = fix_jobs.get((run_id, finding_id), {})
        state = {"status": job.get("status", "none")}
        if job.get("error"):
            state["error"] = job["error"]
        if (directory / "fix.json").is_file():
            fix = json.loads((directory / "fix.json").read_text())
            state.update({k: fix.get(k) for k in ("summary", "changes", "verification", "files", "patch", "trace", "context")})
            state["status"] = job.get("status", "ready") if job.get("status") == "generating" else "ready"
        if (directory / "pr.json").is_file():
            state["pr"] = json.loads((directory / "pr.json").read_text())
        return state

    @app.post("/api/runs/{run_id}/findings/{finding_id}/fix")
    async def start_fix(run_id: str, finding_id: str):
        """SAST·DAST 맥락으로 대상 저장소의 수정안(패치)을 만든다."""
        valid_ids(run_id, finding_id)
        record = record_of(run_id, finished=False)
        if not record:
            raise HTTPException(404, "실행 기록을 찾을 수 없습니다.")
        launch_fix(run_id, finding_id, record, fix_target(record, finding_id))
        return {"status": "generating"}

    @app.get("/api/runs/{run_id}/findings/{finding_id}/fix")
    async def get_fix(run_id: str, finding_id: str):
        valid_ids(run_id, finding_id)
        return fix_state(run_id, finding_id)

    chat_busy: set[tuple[str, str]] = set()

    def chat_scope(run_id: str, finding_id: str):
        valid_ids(run_id, finding_id)
        record = record_of(run_id, finished=False)
        if not record or not any(r.get("finding_id") == finding_id for r in record.get("results", [])):
            raise HTTPException(404, "점검 항목을 찾을 수 없습니다.")
        return record, ChatStore(output_root / run_id / "chat" / finding_id)

    @app.get("/api/runs/{run_id}/findings/{finding_id}/chat")
    async def chat_history(run_id: str, finding_id: str):
        """점검 항목별 개발자 대화 기록."""
        _, store = chat_scope(run_id, finding_id)
        data = store.load()
        return {"messages": data["messages"], "busy": (run_id, finding_id) in chat_busy}

    @app.post("/api/runs/{run_id}/findings/{finding_id}/chat")
    async def chat_ask(run_id: str, finding_id: str, request: Request):
        """점검 기록을 근거로 개발자 질문에 답한다. NDJSON으로 답을 흘려보낸다."""
        record, store = chat_scope(run_id, finding_id)
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, "JSON 본문이 필요합니다.") from None
        question = str((body or {}).get("message", "")).strip()
        if not 1 <= len(question) <= 2000:
            raise HTTPException(400, "질문은 1~2000자여야 합니다.")
        key = (run_id, finding_id)
        if key in chat_busy:
            raise HTTPException(409, "이전 질문에 답하는 중입니다.")
        result = next(r for r in record["results"] if r.get("finding_id") == finding_id)
        target = catalog.targets.get((result.get("evidence") or {}).get("target_id", ""))
        if not target or not target.repo:
            raise HTTPException(409, "대상에 저장소(repo)가 설정되지 않았습니다.")
        fix = fix_state(run_id, finding_id) if result.get("status") in {"REPRODUCED", "BLOCKED"} else None
        context = chat_context(record, finding_id, fix)
        chat_busy.add(key)

        async def stream():
            try:
                async for event in ask_chat(store, question, context, repo_source(target.repo)):
                    yield json.dumps(event, ensure_ascii=False) + "\n"
            finally:
                chat_busy.discard(key)

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    @app.get("/api/runs/{run_id}/findings/{finding_id}/source")
    async def source_lines(run_id: str, finding_id: str, file: str, line: int, context: int = 12):
        """수정 전 원본 코드에서 지정한 줄 위아래를 돌려준다(코드 흐름 상자를 누르면 보여주는 코드)."""
        valid_ids(run_id, finding_id)
        if not re.fullmatch(r"[A-Za-z0-9_./-]{1,200}", file) or ".." in file.split("/") or file.startswith("/"):
            raise HTTPException(400, "허용되지 않은 파일 경로입니다.")
        context = max(3, min(context, 40))
        text = None
        copy = output_root / run_id / "fix" / finding_id / "repo"
        if (copy / ".git").is_dir():
            # 수정안·PR 커밋과 무관하게 최초(base) 커밋의 원본을 읽는다.
            root = subprocess.run(["git", "rev-list", "--max-parents=0", "HEAD"], cwd=copy,
                                  capture_output=True, text=True, timeout=10).stdout.split()
            if root:
                shown = subprocess.run(["git", "show", f"{root[-1]}:{file}"], cwd=copy,
                                       capture_output=True, text=True, timeout=10)
                text = shown.stdout if shown.returncode == 0 else None
        if text is None:
            record = record_of(run_id, finished=False)
            if not record:
                raise HTTPException(404, "실행 기록을 찾을 수 없습니다.")
            repo = repo_of(record, finding_id)
            base = repo_source(repo)
            path = (base / file).resolve()
            if base not in path.parents or not path.is_file() or path.is_symlink():
                raise HTTPException(404, "파일을 찾을 수 없습니다.")
            text = path.read_text(errors="replace")
        lines = text.splitlines()
        start = max(1, line - context)
        end = min(len(lines), line + context)
        return {"file": file, "line": line, "start": start, "total": len(lines),
                "lines": [{"n": n, "text": lines[n - 1]} for n in range(start, end + 1)]}

    @app.post("/api/runs/{run_id}/findings/{finding_id}/pr")
    async def open_pr(run_id: str, finding_id: str, request: Request):
        """사용자가 승인한 수정안으로 PR을 만든다. 원격이 비어 있으면 로컬 커밋까지만 한다."""
        valid_ids(run_id, finding_id)
        try:
            approved = json.loads(await request.body() or b"{}").get("approve") is True
        except ValueError:
            approved = False
        if not approved:
            raise HTTPException(400, "PR 생성에는 사용자 승인(approve: true)이 필요합니다.")
        directory = output_root / run_id / "fix" / finding_id
        if not (directory / "fix.json").is_file():
            raise HTTPException(409, "먼저 수정안을 만드세요.")
        record = record_of(run_id)
        repo = fix_target(record, finding_id)
        # PR 대상 원격 저장소는 프로젝트의 Git 주소를 쓴다(비어 있으면 로컬 커밋까지만).
        project = projects.get_project(record.get("project_id") or DEFAULT_PROJECT)
        repo = repo.model_copy(update={"remote": (project or {}).get("repo_url") or repo.remote})
        fix = json.loads((directory / "fix.json").read_text())
        try:
            result = await asyncio.to_thread(create_pr, fix, repo, directory, run_id)
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from None
        (directory / "pr.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        return result

    @app.post("/api/runs/{run_id}/findings/{finding_id}/reverify")
    async def reverify(run_id: str, finding_id: str, request: Request):
        """같은 에이전트 세션을 이어받아 한 항목을 다시 점검한다(수정 후 재검증)."""
        try:
            if str(UUID(run_id)) != run_id:
                raise ValueError
        except ValueError:
            raise HTTPException(404, "실행 기록을 찾을 수 없습니다.") from None
        source = runs.get(run_id)
        record = ({"report": source["report"], "results": source["results"], "scenario": source["scenario"],
                   "impact": source.get("impact", True), "project_id": source.get("project_id")}
                  if source else disk_run(run_id))
        if not record or (source and source["status"] == "running"):
            raise HTTPException(409, "완료된 점검에서만 재검증할 수 있습니다.")
        result = next((r for r in record.get("results", []) if r.get("finding_id") == finding_id), None)
        inputs = (record.get("report") or record.get("input") or {})
        finding = next((f for f in inputs.get("findings", []) if f.get("id") == finding_id), None)
        evidence = (result or {}).get("evidence") or {}
        if not finding or not evidence.get("agent_session_id"):
            raise HTTPException(404, "이어받을 에이전트 세션이 없는 항목입니다.")
        try:
            options = json.loads(await request.body() or b"{}")
        except ValueError:
            options = {}
        scenario = options.get("scenario") if options.get("scenario") in {s["id"] for s in SCENARIOS} else record.get("scenario")
        args = RunRequest(scenario=scenario, planner="agent", concurrency=1,
                          impact=bool(options.get("impact", record.get("impact", True))),
                          report={"source": inputs.get("source", "fixture"),
                                  "source_label": inputs.get("source_label", "재검증"),
                                  "findings": [finding]})
        resume = {"session_id": evidence["agent_session_id"], "cwd": evidence["agent_cwd"],
                  "hypothesis": evidence.get("hypothesis")}
        new_id = launch(args, {"resume": {finding_id: resume}, "project_id": record.get("project_id") or DEFAULT_PROJECT,
                               "reverify_of": {"run_id": run_id, "finding_id": finding_id}})
        return {"run_id": new_id}

    def get_run(run_id):
        if run_id not in runs:
            raise HTTPException(404, "실행 기록을 찾을 수 없습니다.")
        return runs[run_id]

    @app.get("/api/runs")
    async def list_runs(project: str | None = None):
        # 메모리의 최근 점검과 디스크에 남은 이전 점검을 합쳐 최신순으로 돌려준다.
        items = {run_id: summary(run) | {"live": True} for run_id, run in runs.items()}
        if output_root.is_dir():
            for directory in output_root.iterdir():
                if directory.name in items or directory.is_symlink() or not directory.is_dir():
                    continue
                data = disk_run(directory.name)
                if data:
                    items[directory.name] = {key: data.get(key) for key in (
                        "run_id", "status", "scenario", "planner", "duration_ms", "source_label",
                        "started_at", "finding_count", "counts", "reverify_of")} | {
                        "live": False, "project_id": data.get("project_id") or DEFAULT_PROJECT}
        if project:
            items = {key: item for key, item in items.items() if item.get("project_id") == project}
        ordered = sorted(items.values(), key=lambda item: item.get("started_at") or 0, reverse=True)
        return {"runs": ordered[:50], "max_parallel": MAX_PARALLEL}

    @app.get("/api/runs/{run_id}/view")
    async def view_run(run_id: str):
        try:
            if str(UUID(run_id)) != run_id:
                raise ValueError
        except ValueError:
            raise HTTPException(404, "실행 기록을 찾을 수 없습니다.") from None
        if run_id in runs:
            run = runs[run_id]
            return summary(run) | {"report": run["report"], "live": True}
        data = disk_run(run_id)
        if data is None:
            raise HTTPException(404, "실행 기록을 찾을 수 없습니다.")
        return data | {"report": data.get("input"), "live": False}

    def project_runs(project_id: str) -> list[dict]:
        found = {rid: {"run_id": rid, "results": run["results"], "started_at": run["started_at"],
                       "reverify_of": run.get("reverify_of"), "status": run["status"]}
                 for rid, run in runs.items() if (run.get("project_id") or DEFAULT_PROJECT) == project_id
                 and run["status"] != "running"}
        if output_root.is_dir():
            for directory in output_root.iterdir():
                if directory.name in found or not directory.is_dir() or directory.name in runs:
                    continue
                data = disk_run(directory.name)
                if data and (data.get("project_id") or DEFAULT_PROJECT) == project_id:
                    found[directory.name] = data
        return list(found.values())

    def fix_brief(run_id: str, finding_id: str) -> dict | None:
        state = fix_state(run_id, finding_id)
        if state["status"] == "none":
            return None
        return {"run_id": run_id, "status": state["status"], "files": state.get("files"), "pr": state.get("pr")}

    @app.get("/api/dashboard")
    async def dashboard():
        """전체 프로젝트 현황: 합계, 프로젝트별 최근 점검 결과·조치 현황, 최근 점검."""
        rows, recent = [], []
        for project in projects.list_projects():
            history = sorted(project_runs(project["id"]), key=lambda r: r.get("started_at") or 0)
            vulns = projects.vulnerability_status(history, lambda *_: None)
            states = {key: sum(1 for v in vulns if v["state"] in group) for key, group in
                      {"open": {"open", "reopen"}, "fixed": {"fixed"}, "ok": {"ok"}, "warn": {"warn"},
                       "check": {"check"}}.items()}
            latest = history[-1] if history else None
            rows.append({"id": project["id"], "name": project["name"], "scans": len(history),
                         "last_run_at": latest.get("started_at") if latest else None,
                         "latest": tally(latest.get("input") or latest.get("report"), latest.get("results"))["counts"] if latest else {},
                         "findings": len(vulns), **states})
            for number, run in enumerate(history, 1):
                recent.append({"project_id": project["id"], "project": project["name"], "number": number,
                               "run_id": run["run_id"], "started_at": run.get("started_at"),
                               "reverify": bool(run.get("reverify_of")),
                               "counts": tally(run.get("input") or run.get("report"), run.get("results"))["counts"]})
        recent.sort(key=lambda r: r.get("started_at") or 0, reverse=True)
        totals = {"projects": len(rows), "scans": sum(r["scans"] for r in rows),
                  "findings": sum(r["findings"] for r in rows), "open": sum(r["open"] for r in rows),
                  "fixed": sum(r["fixed"] for r in rows)}
        handled = totals["findings"] - totals["open"]
        totals["fix_rate"] = round(handled / totals["findings"] * 100) if totals["findings"] else 0
        return {"totals": totals, "projects": rows, "recent": recent[:8]}

    @app.get("/api/projects")
    async def list_projects():
        items = []
        for project in projects.list_projects():
            history = project_runs(project["id"])
            vulns = projects.vulnerability_status(history, lambda *_: None)
            items.append(project | {
                "run_count": len(history),
                "last_run_at": max((r.get("started_at") or 0 for r in history), default=None),
                "open": sum(1 for v in vulns if v["state"] in {"open", "reopen"}),
                "fixed": sum(1 for v in vulns if v["state"] == "fixed")})
        return {"projects": items, "targets": sorted(catalog.targets), "default": DEFAULT_PROJECT}

    @app.post("/api/projects")
    async def add_project(request: Request):
        try:
            data = projects.ProjectInput.model_validate_json(await request.body())
        except (ValidationError, ValueError) as exc:
            detail = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else "입력 형식을 확인하세요."
            raise HTTPException(422, detail.replace("Value error, ", "")) from None
        if data.target_id not in catalog.targets:
            raise HTTPException(422, "등록되지 않은 점검 대상입니다.")
        return projects.create_project(data)

    @app.get("/api/projects/{project_id}")
    async def project_detail(project_id: str):
        project = projects.get_project(project_id)
        if not project:
            raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
        history = project_runs(project_id)
        return project | {"vulnerabilities": projects.vulnerability_status(history, fix_brief),
                          "run_count": len(history)}

    @app.get("/api/runs/latest")
    async def latest_run():
        # 새로고침한 화면이 진행 중이거나 마지막으로 끝난 점검을 이어서 보여주도록 한다.
        if not runs:
            raise HTTPException(404, "실행 기록이 없습니다.")
        run = next(reversed(runs.values()))
        return summary(run) | {"report": run["report"], "started_at": run["started_at"]}

    @app.get("/api/runs/{run_id}")
    async def get_summary(run_id: str):
        return summary(get_run(run_id))

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str, request: Request):
        run = get_run(run_id)
        try:
            after = max(0, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            after = 0

        async def stream():
            cursor = min(after, run["sequence"])
            frame_cursors = {}
            while True:
                changed = run["changed"]
                for key, frame in list(run.get("frames", {}).items()):
                    if frame[0] > frame_cursors.get(key, 0):
                        frame_cursors[key] = frame[0]
                        # 결과 이벤트보다 먼저 보내 완료 직후 클라이언트가 닫아도 마지막 화면이 남는다.
                        # id 없이 보내 재연결 시 Last-Event-ID가 결과 이벤트 기준으로 남게 한다.
                        yield f"data: {json.dumps(frame[1], ensure_ascii=False)}\n\n"
                for sequence, event in list(run["events"]):
                    if sequence > cursor:
                        cursor = sequence
                        yield f"id: {sequence}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                last_event = run["events"][-1][0] if run["events"] else 0
                if run["status"] != "running" and cursor >= last_event:
                    break
                if await request.is_disconnected():
                    break
                try:
                    await asyncio.wait_for(changed.wait(), timeout=10)
                except TimeoutError:
                    yield ": 연결 유지\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"X-Accel-Buffering": "no"})

    @app.get("/api/artifacts/{run_id}/{filename}")
    async def artifact(run_id: str, filename: str):
        try:
            if str(UUID(run_id)) != run_id:
                raise ValueError
        except ValueError:
            raise HTTPException(404, "아티팩트를 찾을 수 없습니다.") from None
        if (filename not in {"report.json", "report.html", "report.pdf"}
                and not re.fullmatch(r"[A-Za-z0-9_-]{1,90}\.png", filename)):
            raise HTTPException(404, "허용되지 않은 아티팩트 경로입니다.")
        directory = output_root / run_id
        path = directory / filename
        if (directory.is_symlink() or path.is_symlink() or path.resolve().parent != directory
                or not path.is_file()):
            raise HTTPException(404, "아티팩트를 찾을 수 없습니다.")
        if filename == "report.json":
            return FileResponse(path, media_type="application/json", filename=filename)
        if filename == "report.html":
            return FileResponse(path, media_type="text/html")
        if filename == "report.pdf":
            return FileResponse(path, media_type="application/pdf", filename=f"DAST-report-{run_id[:8]}.pdf",
                                content_disposition_type="inline")
        return FileResponse(path, media_type="image/png")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        index_file = static_root / "index.html"
        if index_file.is_file():
            return FileResponse(index_file, media_type="text/html")
        return HTMLResponse("<html lang='ko'><meta charset='utf-8'><title>로컬 DAST</title>"
                            "<p>제어 API가 준비되었습니다. 제어 화면 파일을 기다리고 있습니다.</p></html>")

    app.mount("/static", StaticFiles(directory=str(static_root), check_dir=False), name="static")
    return app
