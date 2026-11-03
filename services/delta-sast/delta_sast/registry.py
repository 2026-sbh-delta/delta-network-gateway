"""점검 작업 관리. start는 바로 running을 돌려주고, 진행은 get으로 조회한다.

같은 run_id로 다시 부르면 새로 점검하지 않는다(에이전트 재시도 대비).
규칙 캐시는 모든 점검이 같이 쓰고, DAST 결과(feedback)로 굳힌다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .classify import RuleCache
from .contract import BaselineSummary, DastFeedback, FeedbackOutcome, Scan, ScanRequest, Step, Summary
from .judge import LlmClient, judge_all
from .locate import LocateError
from .sarif import annotate_baseline
from .scan import scan
from .secret_judge import attach_actions
from .secret_judge import judge_all as judge_secrets
from .source import SourceError, SourcePolicy, SourceRejected, fetch, scan_root

logger = logging.getLogger(__name__)


class Unsupported(Exception):
    pass


class RequestRejected(Exception):
    pass


class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


class NotReady(Exception):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


class SastRegistry:
    def __init__(self, state_dir: Path, source_policy: SourcePolicy,
                 client_factory: Callable[[], LlmClient | None], max_parallel: int = 2):
        self.state_dir = state_dir
        self.source_policy = source_policy
        self.client_factory = client_factory
        self.cache = RuleCache(state_dir / "cache.json")
        self._scans: dict[str, Scan] = self._load()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()
        self._slots = asyncio.Semaphore(max_parallel)

    # ----- 상태 저장 -----

    @property
    def _state_file(self) -> Path:
        return self.state_dir / "scans.json"

    def _sarif_file(self, run_id: str) -> Path:
        return self.state_dir / "sarif" / f"{run_id}.sarif"

    def _load(self) -> dict[str, Scan]:
        if not self._state_file.exists():
            return {}
        loaded = [Scan.model_validate(x) for x in json.loads(self._state_file.read_text(encoding="utf-8"))]
        return {s.run_id: s.model_copy(update={"state": "failed", "ended_at": _now(), "error": "서비스 재시작으로 중단됐다"})
                if s.state == "running" else s for s in loaded}

    def _save(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        data = [s.model_dump(mode="json") for s in self._scans.values()]
        tmp = self._state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self._state_file)

    def _put(self, scan: Scan) -> Scan:
        self._scans[scan.run_id] = scan
        self._save()
        return scan

    def _step(self, run_id: str, name: str, detail: str = "") -> None:
        cur = self._scans[run_id]
        self._put(cur.model_copy(update={"steps": (*cur.steps, Step(name=name, at=_now(), detail=detail))}))

    # ----- 조회 -----

    def get(self, run_id: str) -> Scan:
        try:
            return self._scans[run_id]
        except KeyError as exc:
            raise NotFound(run_id) from exc

    def list(self) -> tuple[Scan, ...]:
        return tuple(sorted(self._scans.values(), key=lambda s: s.started_at, reverse=True))

    def sarif(self, run_id: str) -> dict:
        scan = self.get(run_id)
        if scan.state != "done":
            raise NotReady(f"점검이 끝나지 않았다({scan.state})")
        return json.loads(self._sarif_file(run_id).read_text(encoding="utf-8"))

    async def wait(self, run_id: str) -> Scan:
        task = self._tasks.get(run_id)
        if task is not None:
            await asyncio.shield(task)
        return self.get(run_id)

    # ----- 점검 -----

    async def start(self, req: ScanRequest) -> Scan:
        if req.mode == "full":
            raise Unsupported("전체 모드는 준비 중이다")
        async with self._lock:
            existing = self._scans.get(req.run_id)
            if existing is not None:
                if (existing.repo, existing.commit, existing.subdir, existing.mode, existing.baseline_run_id) != (
                        req.repo, req.commit, req.subdir, req.mode, req.baseline_run_id):
                    raise Conflict("같은 run_id로 다른 저장소·커밋을 요청했다")
                return existing
            if req.baseline_run_id is not None:
                base = self._scans.get(req.baseline_run_id)
                if base is None or base.state != "done":
                    raise RequestRejected("baseline_run_id는 끝난(done) 점검이어야 한다")
            scan = self._put(Scan(run_id=req.run_id, state="running", repo=req.repo, commit=req.commit,
                                  subdir=req.subdir, baseline_run_id=req.baseline_run_id, mode=req.mode, payload_mode=req.payload_mode,
                                  started_at=_now(), steps=(Step(name="accepted", at=_now()),)))
            self._tasks[req.run_id] = asyncio.create_task(self._run(req))
            return scan

    async def _run(self, req: ScanRequest) -> None:
        work = self.state_dir / "work" / req.run_id
        cleanup = False
        try:
            async with self._slots:
                self._step(req.run_id, "fetch")
                base, cleanup = await asyncio.to_thread(fetch, req.repo, req.commit, work, self.source_policy)
                root = scan_root(base, req.subdir)
                self._step(req.run_id, "scan", "목록 → 경로 → 분류")
                started = time.perf_counter()
                result = await asyncio.to_thread(scan, root, None, self.cache)  # 서버 이벤트 루프를 막지 않게
                pending = sum(1 for _, j in result.findings if j.by == "pending")
                self._step(req.run_id, "judge", f"LLM 판단 대상 {pending}개")
                judge_started = time.perf_counter()
                client = self.client_factory()
                result.llm = await judge_all(result.findings, result.project, client, self.cache, req.payload_mode)
                result.llm |= await judge_secrets(result.secrets, result.project, client, self.cache)
                attach_actions(result.secrets, root)
                result.timings["judge"] = round(time.perf_counter() - judge_started, 3)
                result.timings["total"] = round(time.perf_counter() - started, 3)
                sarif = result.sarif(run_id=req.run_id, commit=req.commit, mode=req.mode,
                                     payload_mode=req.payload_mode,
                                     repo=req.repo if req.repo.startswith("https://") else None)
                baseline = None
                if req.baseline_run_id is not None:
                    baseline = BaselineSummary(**annotate_baseline(sarif, self.sarif(req.baseline_run_id)))
                    sarif["runs"][0]["properties"]["baseline"] = baseline.model_dump()
                self._sarif_file(req.run_id).parent.mkdir(parents=True, exist_ok=True)
                self._sarif_file(req.run_id).write_text(json.dumps(sarif, ensure_ascii=False), encoding="utf-8")
                self.cache.save()
            props = sarif["runs"][0]["properties"]
            summary = Summary(counts=props["counts"], dast_targets=props["dast_targets"],
                              llm=props["llm"], timings=props["timings"], baseline=baseline)
            cur = self._scans[req.run_id]
            self._put(cur.model_copy(update={"state": "done", "summary": summary, "ended_at": _now()}))
            self._step(req.run_id, "done", f"DAST 대상 {summary.dast_targets}개")
        except asyncio.CancelledError:
            raise
        except (SourceRejected, SourceError, LocateError) as exc:
            self._fail(req.run_id, str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("SAST 점검 중 예상하지 못한 오류")
            self._fail(req.run_id, f"예상하지 못한 오류: {type(exc).__name__}")
        finally:
            if cleanup:
                shutil.rmtree(work, ignore_errors=True)

    def _fail(self, run_id: str, error: str) -> None:
        cur = self._scans[run_id]
        self._put(cur.model_copy(update={"state": "failed", "error": error, "ended_at": _now()}))

    # ----- DAST 결과 학습 -----

    def feedback(self, run_id: str, fb: DastFeedback) -> list[FeedbackOutcome]:
        results = {r["partialFingerprints"]["deltaFlow/v1"]: r
                   for r in self.sarif(run_id)["runs"][0]["results"]}
        out = []
        for item in fb.results:
            result = results.get(item.fingerprint)
            if result is None:
                out.append(FeedbackOutcome(fingerprint=item.fingerprint, outcome="unknown",
                                           detail="이 점검의 결과가 아니다"))
                continue
            judgment = result["properties"]["judgment"]
            if judgment["by"] != "llm" or judgment["verdict"] not in ("vulnerable", "safe") or item.status == "ERROR":
                out.append(FeedbackOutcome(fingerprint=item.fingerprint, outcome="skipped",
                                           detail="LLM이 판정한 결과만 규칙으로 굳힌다"))
                continue
            outcome = self.cache.learn_key(
                judgment["cache_key"], [s["name"] for s in result["properties"]["sanitizers"]],
                result["properties"]["dast"]["context"], judgment["verdict"], item.status, run_id)
            out.append(FeedbackOutcome(fingerprint=item.fingerprint, outcome=outcome))
        self.cache.save()
        return out
