"""Connect the operator's immutable DB plan to an approved platform workflow."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field

from delta_platform.core.errors import Problem
from delta_platform.core.schema import workflow_executions, workflows
from delta_platform.core.store import digest
from delta_platform.core.workflows import WorkflowInput, WorkflowStore

PHASES = (
    "planned",
    "preparing",
    "copying",
    "synchronizing",
    "fencing_source",
    "source_fenced",
    "validated",
    "promotion_pending",
    "promoted",
    "cleaned",
    "aborted",
)
TERMINAL = {"cleaned", "aborted"}


class Progress(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    phase: Literal[
        "planned",
        "preparing",
        "copying",
        "synchronizing",
        "fencing_source",
        "source_fenced",
        "validated",
        "promotion_pending",
        "promoted",
        "cleaned",
        "aborted",
    ]
    lag_bytes: int | None = Field(default=None, ge=0)
    validated: bool = False


def payload(plan):
    expected = {
        "version",
        "source_environment_id",
        "target_environment_id",
        "source",
        "target",
        "schema",
        "schema_hash",
        "writer_roles",
        "replication_role",
    }
    if (
        not isinstance(plan, dict)
        or set(plan) != expected
        or plan["version"] != 1
        or digest(plan["schema"]) != plan["schema_hash"]
        or len(json.dumps(plan)) > 1_000_000
    ):
        raise Problem("migration_plan_invalid", "생성한 DB 계획의 형식·해시를 확인하세요.", 422)
    for side in ("source", "target"):
        if (
            set(plan[side]) != {"system_id", "database", "major_version", "operator"}
            or plan[side]["major_version"] != 17
        ):
            raise Problem("migration_plan_invalid", "계획의 DB 신원 형식을 확인하세요.", 422)
    return {"type": "postgresql17", "db_plan": plan}


class MigrationTrackingStore(WorkflowStore):
    def register(self, service_id, build_id, plan, key):
        # DB connections stay in the process environment, never in the workflow.
        return self.create(
            service_id,
            WorkflowInput(
                build_id=build_id,
                source_environment_id=plan["source_environment_id"],
                target_environment_ids=[plan["target_environment_id"]],
                actions=[
                    {
                        "environment_id": plan["target_environment_id"],
                        "kind": "migration_step",
                        "payload_hash": digest(payload(plan)),
                    }
                ],
            ),
            key,
        )

    def prepare(self, workflow_id, plan):
        wf = self.get(workflow_id)
        source = wf["input_snapshot"]["source"]
        if wf["kind"] != "migration" or not source or source["id"] != plan["source_environment_id"]:
            raise Problem("migration_source_mismatch", "계획의 출발 환경이 다릅니다.", 409)
        return self.prepare_execution(
            workflow_id,
            UUID(plan["target_environment_id"]),
            "migration_step",
            payload(plan),
            "postgres:" + digest(plan),
        )

    def record(self, context, progress, *, blocked=False):
        progress = Progress.model_validate(progress).model_dump()
        if (
            progress["phase"] in ("validated", "promotion_pending", "promoted", "cleaned")
            and not progress["validated"]
        ):
            raise Problem(
                "migration_validation_required",
                "DB 검증 결과 없이 승격 완료로 기록하지 않습니다.",
                409,
            )
        workflow_id, run_id = UUID(context["workflow_id"]), UUID(context["run_id"])
        with self.engine.begin() as conn:
            wf, row = self._guard(conn, workflow_id, run_id, require_approval=True)
            if (
                row["kind"] != "migration_step"
                or row["payload"].get("type") != "postgresql17"
                or self._context(wf, row) != context
            ):
                raise Problem(
                    "migration_context_mismatch", "DB 이전 실행의 소유 관계·입력이 다릅니다.", 409
                )
            previous = row["result"]
            if previous:
                if previous.get("type") != "postgres_migration":
                    raise Problem(
                        "migration_result_conflict", "기존 실행 결과 형식이 다릅니다.", 409
                    )
                old, new = previous["phase"], progress["phase"]
                if old in TERMINAL:
                    if (
                        old == new
                        and previous["validated"] == progress["validated"]
                        and not blocked
                    ):
                        return
                    raise Problem(
                        "migration_finished", "완료된 DB 이전 결과를 덮어쓸 수 없습니다.", 409
                    )
                if (new == "aborted" and old in ("promotion_pending", "promoted")) or (
                    new != "aborted" and PHASES.index(new) < PHASES.index(old)
                ):
                    raise Problem(
                        "migration_phase_conflict",
                        "DB 이전 단계를 이전 상태로 덮어쓸 수 없습니다.",
                        409,
                    )
            result = {
                "type": "postgres_migration",
                **progress,
                "blocked": blocked,
                "observed_at": conn.scalar(sa.select(sa.func.clock_timestamp())).isoformat(),
            }
            conn.execute(
                workflow_executions.update()
                .where(workflow_executions.c.id == run_id)
                .values(
                    result=result,
                    status="succeeded"
                    if progress["phase"] == "cleaned"
                    else "aborted"
                    if progress["phase"] == "aborted"
                    else "blocked"
                    if blocked
                    else "running",
                )
            )
            # DB completion is not frontend/backend/traffic migration completion.
            conn.execute(
                workflows.update().where(workflows.c.id == workflow_id).values(status="running")
            )

    def latest(self, service_id, environment_id, *, now=None):
        with self.engine.connect() as conn:
            row = (
                conn.execute(
                    sa.select(workflow_executions, workflows.c.service_id)
                    .join(workflows, workflows.c.id == workflow_executions.c.workflow_id)
                    .where(
                        workflows.c.service_id == service_id,
                        workflow_executions.c.environment_id == environment_id,
                        workflow_executions.c.kind == "migration_step",
                        workflow_executions.c.payload["type"].astext == "postgresql17",
                    )
                    .order_by(
                        workflow_executions.c.created_at.desc(), workflow_executions.c.id.desc()
                    )
                    .limit(1)
                )
                .mappings()
                .first()
            )
        if not row:
            return None
        result = row["result"] or {
            "phase": "planned",
            "lag_bytes": None,
            "validated": False,
            "blocked": False,
            "observed_at": row["created_at"].isoformat(),
        }
        observed = datetime.fromisoformat(result["observed_at"])
        terminal = result["phase"] in TERMINAL
        return {
            "workflow_id": str(row["workflow_id"]),
            "run_id": str(row["id"]),
            "stage": result["phase"],
            "validation": "passed" if result["validated"] else "pending",
            "lag_bytes": result["lag_bytes"],
            "observed_at": observed.isoformat(),
            "status": "blocked"
            if result["blocked"]
            else "completed"
            if terminal
            else "stale"
            if ((now or datetime.now(UTC)) - observed).total_seconds() > 300
            else "ok",
        }


def main():
    from delta_platform.config import Settings
    from delta_platform.core.database import engine_for

    parser = argparse.ArgumentParser(description="검토한 DB 계획을 플랫폼 승인 대기로 등록")
    parser.add_argument("--service-id", required=True, type=UUID)
    parser.add_argument("--build-id", required=True, type=UUID)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--request-key", required=True)
    args = parser.parse_args()
    engine = None
    try:
        if args.plan.stat().st_size > 1_000_000 or not 1 <= len(args.request_key) <= 128:
            raise ValueError("Invalid input size")
        plan = json.loads(args.plan.read_text())
        engine = engine_for(Settings())
        wf, created = MigrationTrackingStore(engine).register(
            args.service_id, args.build_id, plan, args.request_key
        )
        print(
            json.dumps(
                {"workflow_id": str(wf["id"]), "input_hash": wf["input_hash"], "created": created}
            )
        )
    except Problem as exc:
        raise SystemExit(exc.message) from None
    except Exception:
        raise SystemExit("플랫폼 설정·DB 계획·서비스와 빌드 연결을 확인하세요.") from None
    finally:
        if engine is not None:
            engine.dispose()
