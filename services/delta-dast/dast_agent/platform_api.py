"""Durable, narrowly scoped API for the platform's staging checks.

기본 대상은 클라우드별 상시 스테이징(staging-gcp·staging-aws: 고정 LB 뒤에 두고 이미지만 바꾼다)이다.
AWS·GCP를 같은 방식으로 다루기 위해서다. runtime_resource 를 준 GCP 요청만 실행마다 만든 Cloud Run 을 대상으로 한다.
"""

import asyncio
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .adapters import from_sarif, sarif_targets
from . import staging_link
from .contracts import Report, RunRequest

MAX_BYTES = 2_000_000


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Binding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # 빌드·SAST 실행 ID. 오케스트레이터의 작업 키(영숫자·.-_)와 플랫폼의 UUID 를 모두 받는다.
    build_id: str = Field(pattern=r"^[A-Za-z0-9._-]{1,64}$")
    sast_run_id: str = Field(pattern=r"^[A-Za-z0-9._-]{1,64}$")
    commit_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    image: str = Field(min_length=1, max_length=1024)
    image_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    sast_evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    target_url: str = Field(min_length=1, max_length=300)
    execution_cloud: Literal["aws", "gcp", "onprem"] = "aws"
    runtime_resource: str | None = Field(default=None, max_length=300)
    runtime_revision: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def pinned(self):
        if self.execution_cloud == "onprem" and (self.runtime_resource is None or self.runtime_revision is None):
            raise ValueError("On-premise execution requires its runtime resource and Pod UID")
        if self.execution_cloud == "aws" and self.runtime_resource is not None:
            raise ValueError("AWS checks use the registered staging target")
        if not self.image.endswith("@" + self.image_digest):
            raise ValueError("Pinned image required")
        return self


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    binding: Binding
    report: dict


class PlatformRuns:
    def __init__(self, root: Path, catalog, launch, observe, cancel, target_port):
        self.root = root / ".platform"
        self.catalog, self.launch = catalog, launch
        self.observe, self.cancel = observe, cancel
        self.target_port = target_port
        self.lock = asyncio.Lock()

    def staging_target(self, cloud):
        """클라우드별 상시 스테이징 대상. 매핑에 없으면 예전 기본 대상(DAST_PLATFORM_TARGET_ID).

        GCP 를 먼저 상시 스테이징으로 맞췄다. AWS 는 같은 계약을 적용한 뒤 DAST_STAGING_TARGETS 에 "aws" 를 넣는다.
        """
        mapping = json.loads(os.environ.get("DAST_STAGING_TARGETS", '{"gcp": "staging-gcp"}'))
        target_id = mapping.get(cloud)
        if target_id in self.catalog.targets:
            return target_id
        return os.environ.get("DAST_PLATFORM_TARGET_ID", "platform-sandbox")

    def path(self, run_id):
        try:
            if str(UUID(run_id)) != run_id:
                raise ValueError()
        except ValueError:
            raise HTTPException(404, "검사 ID가 올바르지 않습니다.") from None
        return self.root / (run_id + ".json")

    def read(self, run_id):
        path = self.path(run_id)
        return json.loads(path.read_text()) if path.exists() else None

    def write(self, run_id, data):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.path(run_id)
        temporary = path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as out:
            json.dump(data, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)

    def view(self, run_id):
        record = self.read(run_id)
        if not record or "binding" not in record:
            raise HTTPException(404, "플랫폼 검사 기록이 없습니다.")
        live = self.observe(run_id)
        if record.get("cancelled"):
            status = "cancelling" if live and live.get("status") == "running" else "cancelled"
        elif record["eligible_count"] == 0:
            status = "skipped"
        else:
            status = live.get("status", "error") if live else "interrupted"
        results = (live or {}).get("results", [])
        return {
            "run_id": run_id, "binding": record["binding"], "status": status,
            "eligible_count": record["eligible_count"],
            "finding_ids": record["finding_ids"],
            "findings_truncated": record["eligible_count"] > len(record["finding_ids"]),
            "results": [{"finding_id": r.get("finding_id"), "status": r.get("status"),
                         "evidence_sha256": fingerprint(r)} for r in results],
            "evidence_sha256": fingerprint(results),
            "coverage": "sast_xss_candidates_only", "deployment_approved": False,
        }

    async def submit(self, run_id, submission):
        self.path(run_id)
        args = Submission.model_validate(submission)
        binding = args.binding.model_dump(mode="json", exclude_unset=True)
        if fingerprint(args.report) != binding["sast_evidence_sha256"]:
            raise ValueError("SARIF hash mismatch")
        if args.report.get("version") != "2.1.0" or len(args.report.get("runs", [])) != 1:
            raise ValueError("Single SARIF run required")
        targets = sarif_targets(args.report)
        report = Report.model_validate(from_sarif(args.report)) if targets else None
        request_hash = fingerprint(submission)
        async with self.lock:
            record = self.read(run_id)
            if record:
                if record.get("cancelled"):
                    raise HTTPException(409, "취소한 검사 ID는 다시 실행할 수 없습니다.")
                if record.get("request_hash") != request_hash:
                    raise HTTPException(409, "같은 검사 ID의 입력이 다릅니다.")
                return self.view(run_id)
            target_id = self.staging_target(args.binding.execution_cloud)
            if args.binding.execution_cloud == "gcp" and args.binding.runtime_resource is not None:
                from .cloud_run import register

                target_id = await asyncio.to_thread(register, self.catalog, run_id, binding)
            if args.binding.execution_cloud == "onprem":
                from .onprem import register

                target_id = await asyncio.to_thread(register, self.catalog, run_id, binding)
            defined = self.catalog.targets.get(target_id)
            origin = None
            if defined is not None and defined.staging:
                # 상시 스테이징 주소는 스테이징 도구에서 받는다(인프라를 다시 만들면 바뀐다)
                try:
                    origin = await asyncio.to_thread(staging_link.origin, defined.staging)
                except staging_link.StagingUnavailable as exc:
                    raise HTTPException(503, str(exc)) from None
            target = self.catalog.resolve_target(target_id, "vulnerable", self.target_port, origin)
            if target.origin != binding["target_url"]:
                raise ValueError("Registered staging target mismatch")
            record = {"binding": binding, "request_hash": request_hash,
                      "eligible_count": len(targets),
                      "finding_ids": [f.id for f in report.findings] if report else []}
            # Record intent before launching. After a crash, never silently relaunch.
            self.write(run_id, record)
            if report:
                try:
                    self.launch(RunRequest(scenario="vulnerable", planner="agent",
                                           report=report.model_dump(), target_id=target_id,
                                           concurrency=1, impact=False, auto_fix=False),
                                {"platform_binding": binding}, run_id=run_id)
                except Exception:
                    # The persisted request remains interrupted; a new run ID is required.
                    raise
            return self.view(run_id)

    async def stop(self, run_id):
        async with self.lock:
            record = self.read(run_id) or {}
            self.write(run_id, {**record, "cancelled": True})
            # A tombstone prevents a delayed PUT from starting against the next tenant.
            await self.cancel(run_id)
            live = self.observe(run_id)
            if live and live.get("status") == "running":
                raise HTTPException(409, "검사 종료를 기다리고 있습니다.")
            if record.get("binding", {}).get("execution_cloud") == "gcp":
                from .cloud_run import forget

                forget(self.catalog, run_id, record["binding"])
            if record.get("binding", {}).get("execution_cloud") == "onprem":
                from .onprem import forget

                forget(self.catalog, run_id)
            return {"run_id": run_id, "stopped": True}


def install(app, registry):
    @app.get("/api/platform/target")
    async def target():
        target_id = os.environ.get("DAST_PLATFORM_TARGET_ID", "platform-sandbox")
        try:
            registered = registry.catalog.resolve_target(target_id, "vulnerable", registry.target_port)
        except ValueError:
            raise HTTPException(503, "플랫폼 검사 대상을 등록하세요.") from None
        return {"target_id": target_id, "target_url": registered.origin}

    @app.put("/api/platform/runs/{run_id}")
    async def submit(run_id: str, request: Request):
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise HTTPException(415, "JSON 요청이 필요합니다.")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BYTES:
                raise HTTPException(413, "검사 입력이 너무 큽니다.")
        try:
            return await registry.submit(run_id, json.loads(body))
        except (ValidationError, ValueError, KeyError, TypeError, IndexError):
            raise HTTPException(422, "검사 근거와 등록된 대상을 확인하세요.") from None

    @app.get("/api/platform/runs/{run_id}")
    async def status(run_id: str):
        return registry.view(run_id)

    @app.post("/api/platform/runs/{run_id}/cancel")
    async def cancel(run_id: str):
        return await registry.stop(run_id)
