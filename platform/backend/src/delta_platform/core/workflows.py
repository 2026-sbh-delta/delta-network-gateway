"""Immutable workflow plans and execution ownership, shared by web/infra tools."""

import json
import re
from typing import Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.dialects.postgresql import insert

from delta_platform.core.errors import Problem
from delta_platform.core.schema import environments, runs, services, workflow_executions, workflows
from delta_platform.core.store import digest


class ExecutionIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment_id: UUID
    kind: Literal["migration_step", "app_deployment", "infra_apply"]
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class WorkflowInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["deployment", "migration"] = "migration"
    build_id: UUID
    source_environment_id: UUID | None = None
    target_environment_ids: list[UUID] = Field(min_length=1, max_length=2)
    actions: list[ExecutionIntent] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def distinct_targets(self):
        if len(set(self.target_environment_ids)) != len(self.target_environment_ids):
            raise ValueError("Duplicate target")
        if self.source_environment_id in self.target_environment_ids:
            raise ValueError("Source cannot be target")
        if self.kind == "migration" and not self.source_environment_id:
            raise ValueError("Migration source required")
        if any(action.environment_id not in self.target_environment_ids for action in self.actions):
            raise ValueError("Action outside targets")
        return self


class ApprovalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


def identity(env):
    return {
        "id": str(env["id"]),
        "cloud": env["cloud"],
        "region": env["region"],
        "binding_hash": digest(env["binding"]),
    }


def view(row):
    return {
        k: row[k]
        for k in (
            "id",
            "project_id",
            "service_id",
            "kind",
            "status",
            "input_snapshot",
            "input_hash",
            "approved_by",
            "approved_at",
            "created_at",
        )
    }


class WorkflowStore:
    def __init__(self, engine):
        self.engine = engine

    def _workflow(self, conn, workflow_id, *, lock=False):
        query = sa.select(workflows).where(workflows.c.id == workflow_id)
        row = conn.execute(query.with_for_update() if lock else query).mappings().first()
        if not row:
            raise Problem("workflow_not_found", "배포·이전 작업을 찾을 수 없습니다.", 404)
        return row

    def _environment(self, conn, service_id, project_id, environment_id):
        row = (
            conn.execute(
                sa.select(environments)
                .where(
                    environments.c.id == environment_id,
                    environments.c.service_id == service_id,
                    environments.c.project_id == project_id,
                )
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if not row:
            raise Problem("environment_not_owned", "서비스·프로젝트에 속한 환경이 아닙니다.", 409)
        if row["binding"].get("runtime_status") in ("removing", "removed"):
            raise Problem("environment_unavailable", "앱 삭제 중이거나 삭제된 환경입니다.", 409)
        return row

    def create(self, service_id, body, key):
        with self.engine.begin() as conn:
            service = (
                conn.execute(
                    sa.select(services).where(services.c.id == service_id).with_for_update()
                )
                .mappings()
                .first()
            )
            if not service:
                raise Problem("service_not_found", "서비스를 찾을 수 없습니다.", 404)
            build = (
                conn.execute(sa.select(runs).where(runs.c.id == body.build_id)).mappings().first()
            )
            if (
                not build
                or build["service_id"] != service_id
                or build["kind"] != "image_build"
                or build["status"] != "succeeded"
            ):
                raise Problem("build_not_ready", "같은 서비스의 완료된 빌드가 필요합니다.", 409)
            result = build["result"]
            if (
                not result.get("image")
                or not re.fullmatch(r"sha256:[a-f0-9]{64}", result.get("image_digest", ""))
                or not re.fullmatch(r"[a-f0-9]{40}", build["resolved_commit_sha"] or "")
                or not result["image"].endswith("@" + result["image_digest"])
            ):
                raise Problem("image_not_pinned", "검증할 이미지 digest가 필요합니다.", 409)
            project = service["project_id"]

            def get_env(identifier):
                return identity(self._environment(conn, service_id, project, identifier))

            snapshot = {
                "project_id": str(project),
                "service_id": str(service_id),
                "kind": body.kind,
                "build_id": str(body.build_id),
                "repository_url": service["repository_url"],
                "commit_sha": build["resolved_commit_sha"],
                "image": result["image"],
                "image_digest": result["image_digest"],
                "source": get_env(body.source_environment_id)
                if body.source_environment_id
                else None,
                "targets": [
                    get_env(identifier)
                    for identifier in sorted(body.target_environment_ids, key=str)
                ],
                "actions": sorted(
                    [action.model_dump(mode="json") for action in body.actions],
                    key=lambda action: (
                        action["environment_id"],
                        action["kind"],
                        action["payload_hash"],
                    ),
                ),
            }
            hashed = digest(snapshot)
            row = (
                conn.execute(
                    insert(workflows)
                    .values(
                        id=uuid4(),
                        project_id=project,
                        service_id=service_id,
                        kind=body.kind,
                        status="planned",
                        input_snapshot=snapshot,
                        input_hash=hashed,
                        request_key=key,
                    )
                    .on_conflict_do_nothing()
                    .returning(workflows)
                )
                .mappings()
                .first()
            )
            if row:
                return view(row), True
            row = (
                conn.execute(
                    sa.select(workflows).where(
                        workflows.c.service_id == service_id, workflows.c.request_key == key
                    )
                )
                .mappings()
                .one()
            )
            if row["input_hash"] != hashed:
                raise Problem(
                    "idempotency_conflict", "같은 요청 키에 다른 계획이 들어왔습니다.", 409
                )
            return view(row), False

    def get(self, workflow_id):
        with self.engine.connect() as conn:
            return view(self._workflow(conn, workflow_id))

    def list(self, service_id):
        with self.engine.connect() as conn:
            if not conn.scalar(sa.select(services.c.id).where(services.c.id == service_id)):
                raise Problem("service_not_found", "서비스를 찾을 수 없습니다.", 404)
            rows = conn.execute(
                sa.select(workflows)
                .where(workflows.c.service_id == service_id)
                .order_by(workflows.c.created_at.desc())
                .limit(20)
            ).mappings()
            return {"items": [view(row) for row in rows]}

    def _check_targets(self, conn, workflow):
        snapshot = workflow["input_snapshot"]
        if digest(snapshot) != workflow["input_hash"]:
            raise Problem("plan_integrity_error", "저장한 계획의 해시가 다릅니다.", 409)
        refs = [*snapshot["targets"], *([snapshot["source"]] if snapshot["source"] else [])]
        for reference in sorted(refs, key=lambda item: item["id"]):
            row = self._environment(
                conn, workflow["service_id"], workflow["project_id"], UUID(reference["id"])
            )
            if identity(row) != reference:
                raise Problem(
                    "plan_target_changed", "환경 연결이 변경됐습니다. 새 계획을 검토하세요.", 409
                )

    def approve(self, workflow_id, expected_hash, actor):
        with self.engine.begin() as conn:
            row = self._workflow(conn, workflow_id, lock=True)
            if row["input_hash"] != expected_hash:
                raise Problem("approval_mismatch", "화면에서 검토한 계획과 다릅니다.", 409)
            if row["status"] not in ("planned", "approved"):
                raise Problem("workflow_state_conflict", "현재 단계에서는 승인할 수 없습니다.", 409)
            self._check_targets(conn, row)
            if row["status"] == "approved":
                return view(row)
            changed = (
                conn.execute(
                    workflows.update()
                    .where(workflows.c.id == workflow_id)
                    .values(
                        status="approved",
                        approved_by=actor,
                        approved_at=sa.func.now(),
                    )
                    .returning(workflows)
                )
                .mappings()
                .one()
            )
            return view(changed)

    def prepare_execution(self, workflow_id, environment_id, kind, payload, key):
        # Worker/tool integration only. Untrusted user input is not a shell command.
        if kind not in ("ai_assessment", "migration_step", "app_deployment", "infra_apply"):
            raise Problem("execution_kind_invalid", "지원하지 않는 실행 종류입니다.", 422)
        if len(json.dumps(payload)) > 1_000_000:
            raise Problem("execution_payload_large", "실행 입력이 너무 큽니다.", 422)
        with self.engine.begin() as conn:
            wf = self._workflow(conn, workflow_id, lock=True)
            self._check_targets(conn, wf)
            if wf["status"] in ("succeeded", "failed"):
                raise Problem(
                    "workflow_finished", "완료된 작업에는 새 실행을 추가할 수 없습니다.", 409
                )
            if str(environment_id) not in [e["id"] for e in wf["input_snapshot"]["targets"]]:
                raise Problem("execution_target_invalid", "계획의 이전 대상이 아닙니다.", 409)
            if kind != "ai_assessment" and (
                wf["status"] not in ("approved", "running") or not wf["approved_at"]
            ):
                raise Problem("approval_required", "검토한 계획의 사람 승인이 필요합니다.", 409)
            self._check_action(wf, environment_id, kind, payload)
            snapshot = {"environment_id": str(environment_id), "kind": kind, "body": payload}
            hashed = digest(snapshot)
            row = (
                conn.execute(
                    insert(workflow_executions)
                    .values(
                        id=uuid4(),
                        workflow_id=workflow_id,
                        environment_id=environment_id,
                        kind=kind,
                        payload=payload,
                        payload_hash=hashed,
                        request_key=key,
                    )
                    .on_conflict_do_nothing()
                    .returning(workflow_executions)
                )
                .mappings()
                .first()
            )
            if not row:
                row = (
                    conn.execute(
                        sa.select(workflow_executions).where(
                            workflow_executions.c.workflow_id == workflow_id,
                            workflow_executions.c.request_key == key,
                        )
                    )
                    .mappings()
                    .one()
                )
                if row["payload_hash"] != hashed:
                    raise Problem(
                        "idempotency_conflict", "변경한 입력은 새 실행 ID가 필요합니다.", 409
                    )
            return self._context(wf, row)

    @staticmethod
    def _check_action(wf, environment_id, kind, payload):
        if kind == "ai_assessment":
            return
        action = {
            "environment_id": str(environment_id),
            "kind": kind,
            "payload_hash": digest(payload),
        }
        if action not in wf["input_snapshot"]["actions"]:
            raise Problem(
                "action_not_approved",
                "실행 내용이 승인한 계획에 없습니다. 새 계획을 검토하세요.",
                409,
            )

    @staticmethod
    def _context(wf, execution):
        source = wf["input_snapshot"]["source"]
        target = next(
            e
            for e in wf["input_snapshot"]["targets"]
            if e["id"] == str(execution["environment_id"])
        )
        return {
            "project_id": str(wf["project_id"]),
            "service_id": str(wf["service_id"]),
            "workflow_id": str(wf["id"]),
            "run_id": str(execution["id"]),
            "environment_id": str(execution["environment_id"]),
            "source_environment_id": source["id"] if source else None,
            "target_provider": target["cloud"],
            "source_provider": source["cloud"] if source else None,
            "repository_url": wf["input_snapshot"]["repository_url"],
            "commit_sha": wf["input_snapshot"]["commit_sha"],
            "image_digest": wf["input_snapshot"]["image_digest"],
            "plan_hash": wf["input_hash"],
            "payload_hash": digest(execution["payload"]),
        }

    def _guard(self, conn, workflow_id, run_id, *, require_approval=False):
        wf = self._workflow(conn, workflow_id, lock=True)
        self._check_targets(conn, wf)
        execution = (
            conn.execute(
                sa.select(workflow_executions)
                .where(
                    workflow_executions.c.id == run_id,
                    workflow_executions.c.workflow_id == workflow_id,
                )
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if not execution:
            raise Problem("execution_not_owned", "작업과 실행 ID가 일치하지 않습니다.", 409)
        expected = digest(
            {
                "environment_id": str(execution["environment_id"]),
                "kind": execution["kind"],
                "body": execution["payload"],
            }
        )
        if expected != execution["payload_hash"]:
            raise Problem("execution_input_changed", "저장된 실행 입력이 변경됐습니다.", 409)
        self._check_action(wf, execution["environment_id"], execution["kind"], execution["payload"])
        if require_approval and (
            not wf["approved_at"] or wf["status"] not in ("approved", "running")
        ):
            raise Problem("approval_required", "승인 상태를 확인하세요.", 409)
        return wf, execution

    def guard(self, workflow_id, run_id, *, require_approval=False):
        with self.engine.begin() as conn:
            wf, execution = self._guard(
                conn, workflow_id, run_id, require_approval=require_approval
            )
            return self._context(wf, execution), execution["payload"]

    def executions(self, workflow_id):
        with self.engine.connect() as conn:
            self._workflow(conn, workflow_id)
            rows = conn.execute(
                sa.select(workflow_executions)
                .where(
                    workflow_executions.c.workflow_id == workflow_id,
                )
                .order_by(workflow_executions.c.created_at.desc())
                .limit(50)
            ).mappings()
            # Raw tool payloads/results are never a public API response.
            return {
                "items": [
                    {k: row[k] for k in ("id", "environment_id", "kind", "status", "created_at")}
                    for row in rows
                ]
            }

    def complete_execution(self, workflow_id, run_id, returned_context, result):
        if len(json.dumps(result)) > 1_000_000:
            raise Problem("execution_result_large", "실행 결과가 너무 큽니다.", 422)
        with self.engine.begin() as conn:
            wf, row = self._guard(conn, workflow_id, run_id)
            if self._context(wf, row) != returned_context:
                raise Problem(
                    "execution_result_mismatch", "실행 결과의 고객·환경·입력 연결이 다릅니다.", 409
                )
            if row["status"] == "succeeded" and row["result"] != result:
                raise Problem(
                    "execution_result_conflict", "같은 실행의 기존 결과를 덮어쓸 수 없습니다.", 409
                )
            conn.execute(
                workflow_executions.update()
                .where(workflow_executions.c.id == run_id)
                .values(status="succeeded", result=result)
            )
