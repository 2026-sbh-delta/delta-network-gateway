"""Bind read-only Diff to immutable plans and the existing public comparison table."""

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import sqlalchemy as sa
from fastapi import Request
from pydantic import BaseModel, ConfigDict

from delta_platform.adapters.workload_inventory import COVERAGE, read_for_diff
from delta_platform.core.environments import EnvironmentStore
from delta_platform.core.errors import Problem
from delta_platform.core.inputs import request_key
from delta_platform.core.schema import workflow_executions
from delta_platform.core.store import digest


def text(value, limit=600):
    return str(value or "")[:limit]


def projection(result, context, warnings):
    assessment = result["assessment"]
    if result["context"] != context or assessment["assessment_id"] != context["run_id"]:
        raise ValueError("comparison identity mismatch")
    if assessment["kind"] != "migration_mapping" or assessment["verdict"] not in (
        "pass",
        "fix",
        "needs_human",
    ):
        raise ValueError("comparison kind mismatch")
    rows = assessment["mapping"]["rows"]
    if not isinstance(rows, list) or not rows or len(rows) > 200:
        raise ValueError("bounded mapping rows required")
    grades = {
        "exact": "satisfied",
        "approximate": "partial",
        "split": "partial",
        "gap": "unsupported",
        "unknown": "needs_check",
    }
    differences, gaps = [], list(warnings)
    for row in rows:
        grade = row["grade"]
        if grade not in grades:
            raise ValueError("unknown mapping grade")
        treatment = "; ".join(text(s, 300) for s in row.get("differences", [])[:5])
        differences.append(
            {
                "item": text(row["resource"], 80),
                "source": text(row["source"]),
                "target": text(row.get("target")),
                "treatment": treatment
                or "제안된 대응이며 대상의 실제 구성·호출은 별도 확인합니다.",
                "judgment": grades[grade],
                "before": text(row["source"]),
                "after": None,
            }
        )
        if grade in ("gap", "unknown"):
            gaps.append(text(row["source"]) + ": " + (treatment or "대응·근거 확인 필요"))
    if assessment["verdict"] == "pass" and any(r["grade"] in ("gap", "unknown") for r in rows):
        raise ValueError("Diff pass contradicts its rows")
    if warnings:
        differences.append(
            {
                "item": "관측 범위",
                "source": "등록된 앱 리소스",
                "target": "추가 확인 필요",
                "treatment": " ".join(warnings),
                "judgment": "needs_check",
                "before": None,
                "after": None,
            }
        )
    verdict = (
        "needs_human" if warnings and assessment["verdict"] == "pass" else assessment["verdict"]
    )
    return {
        "workflow_id": context["workflow_id"],
        "environment_id": context["environment_id"],
        "plan_hash": context["plan_hash"],
        "assessment_id": context["run_id"],
        "summary": f"워크로드 차분 {verdict} · {len(rows)}개 항목",
        "impact": "읽기 전용 분석 결과입니다. 자원 생성·운영 배포·DNS 변경을 실행하지 않았습니다.",
        "remaining_gaps": gaps[:100],
        "differences": differences,
        "changes": [],
    }


class WorkflowDiff:
    def __init__(self, settings, store, *, inventory=read_for_diff, transport=None):
        self.settings, self.store, self.inventory, self.transport = (
            settings,
            store,
            inventory,
            transport,
        )
        self.environments = EnvironmentStore(store.engine)

    def _request(self, body):
        url = (
            getattr(self.settings, "orchestrator_url", "")
            or os.environ.get("PLATFORM_ORCHESTRATOR_URL", "")
        ).rstrip("/")
        secret = getattr(self.settings, "orchestrator_password", None)
        password = (
            secret.get_secret_value()
            if secret
            else os.environ.get("PLATFORM_ORCHESTRATOR_PASSWORD", "")
        )
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.path
            or parts.query
            or parts.fragment
            or parts.username
            or parts.password
            or len(password) < 16
        ):
            raise Problem("diff_not_configured", "운영자의 HTTPS 차분 연결 설정을 확인하세요.", 503)
        with httpx.Client(
            timeout=55,
            follow_redirects=False,
            trust_env=False,
            transport=self.transport,
            headers={"Origin": url},
        ) as client:
            login = client.post(url + "/login", data={"password": password})
            if login.status_code != 303:
                raise Problem("diff_auth_failed", "차분 연결의 서비스 인증을 확인하세요.", 503)
            with client.stream("POST", url + "/api/platform-diff", json=body) as response:
                if response.status_code != 200:
                    raise Problem(
                        "diff_unavailable",
                        f"차분 조회 미완료 (HTTP {response.status_code}). 등록 환경·연결을 확인하세요.",
                        502,
                    )
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 256_000:
                        raise ValueError("bounded Diff response required")
        return json.loads(raw)

    def _failure(self, workflow_id, environment_id, payload, key, error):
        try:
            context = self.store.prepare_execution(
                workflow_id, environment_id, "ai_assessment", payload, key
            )
            with self.store.engine.begin() as conn:
                conn.execute(
                    workflow_executions.update()
                    .where(
                        workflow_executions.c.id == UUID(context["run_id"]),
                        workflow_executions.c.status != "succeeded",
                    )
                    .values(
                        status="failed",
                        result={"error": {"code": error.code, "message": error.message}},
                    )
                )
        except Problem:
            pass

    def execute(self, workflow_id, environment_id, key):
        workflow_id, environment_id = UUID(str(workflow_id)), UUID(str(environment_id))
        wf = self.store.get(workflow_id)
        plan = wf["input_snapshot"]
        source = plan.get("source")
        target = next((e for e in plan["targets"] if e["id"] == str(environment_id)), None)
        if not source or not target:
            raise Problem("diff_plan_required", "출발·대상이 고정된 이전 계획이 필요합니다.", 409)
        payload = {
            "kind": "workload_diff",
            "source_binding_hash": source["binding_hash"],
            "target_binding_hash": target["binding_hash"],
        }
        fail_key = "diff-error:" + digest({"key": key, "payload": payload})
        try:
            source_env = self.environments.get(UUID(str(wf["service_id"])), UUID(source["id"]))
            target_env = self.environments.get(UUID(str(wf["service_id"])), environment_id)
            body = {
                "target_region": target["region"],
                "source_instance_ids": [
                    i["id"] for i in source_env["binding"].get("instances", [])
                ],
            }
            warnings = []
            for prefix, env in (("source", source_env), ("target", target_env)):
                if env["cloud"] == "onprem":
                    manifest, observation = self.inventory(self.settings, env)
                    body[prefix + "_manifests"] = manifest
                    payload[prefix + "_snapshot"] = hashlib.sha256(manifest.encode()).hexdigest()
                    warnings.append(COVERAGE)
                    if observation:
                        warnings.append(observation)
            execution_key = "diff:" + digest({"key": key, "payload": payload})
            context = self.store.prepare_execution(
                workflow_id, environment_id, "ai_assessment", payload, execution_key
            )
            context, _ = self.store.guard(workflow_id, UUID(context["run_id"]))
            with self.store.engine.connect() as conn:
                existing = (
                    conn.execute(
                        sa.select(workflow_executions.c.status, workflow_executions.c.result).where(
                            workflow_executions.c.id == UUID(context["run_id"])
                        )
                    )
                    .mappings()
                    .one()
                )
            if existing["status"] == "succeeded":
                return existing["result"]["assessment"]
            result = self._request({**body, "context": context})
            for prefix in ("source",):
                if (
                    prefix + "_manifests" in body
                    and result["source"].get("sha256") != payload[prefix + "_snapshot"]
                ):
                    raise ValueError("snapshot changed")
            view = projection(result, context, warnings)
            self.store.complete_execution(
                workflow_id,
                UUID(context["run_id"]),
                context,
                {
                    "assessment": view,
                    "source": result["source"],
                    "verdict": result["assessment"]["verdict"],
                },
            )
            return view
        except Problem as exc:
            fail_key = "diff-error:" + digest({"key": key, "payload": payload})
            self._failure(workflow_id, environment_id, payload, fail_key, exc)
            raise
        except Exception:
            error = Problem(
                "diff_failed",
                "차분 조회·결과 연결 검증을 완료하지 못했습니다. 보안 통과로 처리하지 않습니다.",
                502,
            )
            fail_key = "diff-error:" + digest({"key": key, "payload": payload})
            self._failure(workflow_id, environment_id, payload, fail_key, error)
            raise error from None

    def analyze_plan(self, workflow_id):
        wf = self.store.get(UUID(str(workflow_id)))
        if not wf["input_snapshot"].get("source"):
            return

        def one(target):
            try:
                self.execute(
                    workflow_id, target["id"], "plan:" + str(workflow_id) + ":" + target["id"]
                )
            except Problem:
                pass  # Failure is visible in this plan's execution list; no release is started.

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(one, wf["input_snapshot"]["targets"]))

    def public_results(self, workflow_id, original):
        workflow_id = UUID(str(workflow_id))
        with self.store.engine.connect() as conn:
            rows = (
                conn.execute(
                    sa.select(workflow_executions)
                    .where(workflow_executions.c.workflow_id == workflow_id)
                    .order_by(workflow_executions.c.created_at.desc())
                    .limit(50)
                )
                .mappings()
                .all()
            )
        lookup = {}
        for row in rows:
            if row["payload"].get("kind") != "workload_diff" or row["status"] != "succeeded":
                continue
            context, _ = self.store.guard(workflow_id, row["id"])
            view = (row["result"] or {}).get("assessment")
            if (
                view
                and view.get("assessment_id") == context["run_id"]
                and view.get("workflow_id") == str(workflow_id)
                and view.get("environment_id") == context["environment_id"]
                and view.get("plan_hash") == context["plan_hash"]
            ):
                lookup[str(row["id"])] = view
        return {
            "items": [
                {
                    **item,
                    **(
                        {"assessment": lookup[str(item["id"])]} if str(item["id"]) in lookup else {}
                    ),
                }
                for item in original["items"]
            ]
        }


class DiffInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment_id: UUID


def install(protected, workflows, settings):
    """Keep existing routes/authentication and the already-deployed web assets."""
    service = WorkflowDiff(settings, workflows)
    get_route = next(
        r
        for r in protected.routes
        if getattr(r, "path", "") == "/workflows/{workflow_id}/executions"
    )
    create_route = next(
        r
        for r in protected.routes
        if getattr(r, "path", "") == "/services/{service_id}/workflows" and "POST" in r.methods
    )
    original_get, original_create = get_route.dependant.call, create_route.dependant.call

    @wraps(original_get)
    def projected(**kwargs):
        return service.public_results(kwargs["workflow_id"], original_get(**kwargs))

    @wraps(original_create)
    def analyzed(**kwargs):
        response = original_create(**kwargs)
        configured = getattr(settings, "orchestrator_url", "") or os.environ.get(
            "PLATFORM_ORCHESTRATOR_URL"
        )
        if response.status_code == 201 and configured:
            service.analyze_plan(json.loads(response.body)["id"])
        return response

    get_route.endpoint = get_route.dependant.call = projected
    create_route.endpoint = create_route.dependant.call = analyzed

    @protected.post("/workflows/{workflow_id}/diff")
    def run_diff(workflow_id: UUID, body: DiffInput, request: Request):
        return service.execute(
            workflow_id, body.environment_id, request_key(request.headers.get("idempotency-key"))
        )

    return service
