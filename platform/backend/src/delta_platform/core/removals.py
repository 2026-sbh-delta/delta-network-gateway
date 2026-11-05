"""Durable, explicit removal requests using the existing leased worker queue."""

from uuid import UUID, uuid4

import sqlalchemy as sa

from delta_platform.core.errors import Problem
from delta_platform.core.runtime import OnpremRuntime
from delta_platform.core.schema import environments, runs, services, workflows
from delta_platform.core.store import RUN_FIELDS, append_event, digest, select_fields


def runtime_binding(env):
    if env["cloud"] != "onprem" or not env["binding"].get("runtime"):
        raise Problem(
            "runtime_unregistered", "운영자가 삭제할 앱 자원을 먼저 연결해야 합니다.", 409
        )
    return OnpremRuntime.model_validate(env["binding"]["runtime"]).model_dump(mode="json")


class RemovalStore:
    def __init__(self, engine):
        self.engine = engine

    def latest(self, conn, service_id, environment_id):
        return (
            conn.execute(
                sa.select(runs)
                .where(
                    runs.c.service_id == service_id,
                    runs.c.kind == "runtime_remove",
                    runs.c.input_snapshot["environment_id"].astext == str(environment_id),
                )
                .order_by(runs.c.created_at.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )

    def preview(self, env, target):
        with self.engine.connect() as conn:
            run = self.latest(conn, env["service_id"], env["id"])
        try:
            runtime = runtime_binding(env)
        except Problem as exc:
            return {
                "configured": False,
                "reason": exc.message,
                "resources": [],
                "run": None,
                "state": "unregistered",
                "binding_hash": None,
            }
        configured = bool(
            target
            and runtime["cluster_id"] == target["cluster_id"]
            and runtime["namespace"] in target["namespaces"]
        )
        return {
            "configured": configured,
            "reason": None if configured else "온프레미스 앱 삭제 실행기가 연결되지 않았습니다.",
            "resources": [{"kind": r["kind"], "name": r["name"]} for r in runtime["resources"]],
            "binding_hash": digest({"runtime": runtime, "target": target}),
            "state": env["binding"].get("runtime_status", "active"),
            "run": select_fields(run, RUN_FIELDS) if run else None,
        }

    def enqueue(self, service_id, environment_id, name, expected_hash, key, target):
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
            if service["name"] != name:
                raise Problem("delete_name_mismatch", "서비스 이름이 일치하지 않습니다.", 409)
            plans = list(
                conn.execute(
                    sa.select(workflows)
                    .where(workflows.c.service_id == service_id)
                    .order_by(workflows.c.id)
                    .with_for_update()
                ).mappings()
            )
            env = (
                conn.execute(
                    sa.select(environments)
                    .where(
                        environments.c.id == environment_id, environments.c.service_id == service_id
                    )
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if not env:
                raise Problem("environment_not_found", "이 서비스에 연결된 환경이 아닙니다.", 404)
            runtime = runtime_binding(env)
            if (
                runtime["cluster_id"] != target["cluster_id"]
                or runtime["namespace"] not in target["namespaces"]
            ):
                raise Problem(
                    "runtime_target_mismatch", "등록된 온프레미스와 연결 설정이 다릅니다.", 409
                )
            if expected_hash != digest({"runtime": runtime, "target": target}):
                raise Problem(
                    "removal_plan_changed", "삭제 대상이 변경됐습니다. 다시 확인하세요.", 409
                )
            request_hash = digest(
                {"environment_id": str(environment_id), "binding_hash": expected_hash}
            )
            existing = (
                conn.execute(
                    sa.select(runs).where(
                        runs.c.service_id == service_id,
                        runs.c.kind == "runtime_remove",
                        runs.c.request_key == key,
                    )
                )
                .mappings()
                .first()
            )
            if existing:
                if existing["request_hash"] != request_hash:
                    raise Problem(
                        "idempotency_conflict", "같은 요청 키의 삭제 대상이 다릅니다.", 409
                    )
                return select_fields(existing, RUN_FIELDS), False
            latest = self.latest(conn, service_id, environment_id)
            if env["binding"].get("runtime_status") == "removed":
                return select_fields(latest, RUN_FIELDS), False
            active = conn.scalar(
                sa.select(runs.c.id)
                .where(runs.c.service_id == service_id, runs.c.status.in_(["queued", "running"]))
                .limit(1)
            )
            if active:
                if latest and latest["id"] == active:
                    return select_fields(latest, RUN_FIELDS), False
                raise Problem("service_busy", "진행 중인 작업을 마친 뒤 앱을 삭제하세요.", 409)
            if any(plan["status"] in ("approved", "running") for plan in plans):
                raise Problem(
                    "workflow_busy", "승인·실행 중인 배포 또는 이전 계획이 있습니다.", 409
                )
            row = (
                conn.execute(
                    runs.insert()
                    .values(
                        id=uuid4(),
                        service_id=service_id,
                        kind="runtime_remove",
                        status="queued",
                        input_snapshot={
                            "environment_id": str(environment_id),
                            "runtime": runtime,
                            "target": target,
                            "binding_hash": expected_hash,
                        },
                        resolved_commit_sha=None,
                        request_key=key,
                        request_hash=request_hash,
                    )
                    .returning(runs)
                )
                .mappings()
                .one()
            )
            conn.execute(
                environments.update()
                .where(environments.c.id == environment_id)
                .values(
                    binding={**env["binding"], "runtime_status": "removing"},
                    updated_at=sa.func.now(),
                )
            )
            append_event(
                conn,
                row["id"],
                "queued",
                "온프레미스 실행 앱 삭제를 접수했습니다. DB 데이터는 보존합니다.",
            )
            return select_fields(row, RUN_FIELDS), True

    def complete(self, conn, row):
        environment_id = UUID(row["input_snapshot"]["environment_id"])
        env = (
            conn.execute(
                sa.select(environments)
                .where(
                    environments.c.id == environment_id,
                    environments.c.service_id == row["service_id"],
                )
                .with_for_update()
            )
            .mappings()
            .one()
        )
        if runtime_binding(env) != row["input_snapshot"]["runtime"]:
            raise ValueError("Runtime binding changed during removal")
        conn.execute(
            environments.update()
            .where(environments.c.id == environment_id)
            .values(
                binding={**env["binding"], "runtime_status": "removed"}, updated_at=sa.func.now()
            )
        )


def assert_no_removal(conn, service_id):
    if conn.scalar(
        sa.select(environments.c.id)
        .where(
            environments.c.service_id == service_id,
            environments.c.binding["runtime_status"].astext == "removing",
        )
        .limit(1)
    ):
        raise Problem("runtime_removal_pending", "온프레미스 앱 삭제·정리를 먼저 완료하세요.", 409)
