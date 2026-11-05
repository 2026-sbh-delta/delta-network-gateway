"""Delete resources before records, retaining the immutable request across retries."""

from uuid import uuid4

import sqlalchemy as sa

from delta_platform.core.errors import Problem
from delta_platform.core.schema import (
    deployment_plans,
    environments,
    events,
    runs,
    services,
    workflow_executions,
    workflows,
)
from delta_platform.core.store import RUN_FIELDS, append_event, digest, select_fields


def delete_records(conn, service_id):
    owned_runs = sa.select(runs.c.id).where(runs.c.service_id == service_id)
    owned_workflows = sa.select(workflows.c.id).where(workflows.c.service_id == service_id)
    conn.execute(
        workflow_executions.delete().where(workflow_executions.c.workflow_id.in_(owned_workflows))
    )
    conn.execute(workflows.delete().where(workflows.c.service_id == service_id))
    conn.execute(deployment_plans.delete().where(deployment_plans.c.service_id == service_id))
    conn.execute(environments.delete().where(environments.c.service_id == service_id))
    conn.execute(events.delete().where(events.c.run_id.in_(owned_runs)))
    conn.execute(runs.delete().where(runs.c.service_id == service_id))
    conn.execute(services.delete().where(services.c.id == service_id))


class ProjectRemovalStore:
    def __init__(self, engine):
        self.engine = engine

    def latest(self, conn, service_id):
        return (
            conn.execute(
                sa.select(runs)
                .where(runs.c.service_id == service_id, runs.c.kind == "service_remove")
                .order_by(runs.c.created_at.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )

    def status(self, service_id):
        with self.engine.connect() as conn:
            if not conn.scalar(sa.select(services.c.id).where(services.c.id == service_id)):
                return {"status": "deleted", "run": None}
            row = self.latest(conn, service_id)
            return {
                "status": row["status"] if row else "idle",
                "run": select_fields(row, RUN_FIELDS) if row else None,
            }

    def enqueue(self, service_id, name, settings):
        with self.engine.begin() as conn:
            service = (
                conn.execute(
                    sa.select(services).where(services.c.id == service_id).with_for_update()
                )
                .mappings()
                .first()
            )
            if not service:
                return None
            if service["name"] != name:
                raise Problem("delete_name_mismatch", "프로젝트 이름이 일치하지 않습니다.", 409)
            latest = self.latest(conn, service_id)
            if latest and latest["status"] in ("queued", "running"):
                return {"status": latest["status"], "run": select_fields(latest, RUN_FIELDS)}
            if conn.scalar(
                sa.select(runs.c.id)
                .where(runs.c.service_id == service_id, runs.c.status.in_(("queued", "running")))
                .limit(1)
            ):
                raise Problem(
                    "service_busy", "진행 중인 작업과 자원 정리가 끝난 뒤 삭제하세요.", 409
                )
            plans = list(
                conn.execute(
                    sa.select(workflows)
                    .where(workflows.c.service_id == service_id)
                    .order_by(workflows.c.id)
                    .with_for_update()
                ).mappings()
            )
            if any(p["status"] in ("approved", "running") for p in plans):
                raise Problem("workflow_busy", "승인·실행 중인 계획을 먼저 종료하세요.", 409)
            if conn.scalar(
                sa.select(workflow_executions.c.id)
                .where(
                    workflow_executions.c.workflow_id.in_([p["id"] for p in plans]),
                    workflow_executions.c.status == "prepared",
                )
                .limit(1)
            ):
                raise Problem("workflow_busy", "진행 중인 외부 실행을 먼저 종료하세요.", 409)
            # A backend project may still be used in another project's fullstack deployment.
            others = list(
                conn.execute(
                    sa.select(runs.c.input_snapshot).where(
                        runs.c.service_id != service_id, runs.c.kind == "service_deploy"
                    )
                ).scalars()
            )
            if any(
                c.get("service_id") == str(service_id)
                for item in others
                for c in item.get("components", [])
            ):
                raise Problem(
                    "service_in_use",
                    "다른 프로젝트 배포에서 이 서비스를 사용 중입니다. 해당 배포를 먼저 정리하세요.",
                    409,
                )
            if latest:
                snapshot = latest["input_snapshot"]
            else:
                snapshot = self.snapshot(conn, service, settings)
            if not any(snapshot[k] for k in ("runtimes", "deployments", "images")):
                delete_records(conn, service_id)
                return None
            identifier = uuid4()
            row = (
                conn.execute(
                    runs.insert()
                    .values(
                        id=identifier,
                        service_id=service_id,
                        kind="service_remove",
                        status="queued",
                        request_key=str(identifier),
                        request_hash=digest(snapshot),
                        input_snapshot=snapshot,
                    )
                    .returning(runs)
                )
                .mappings()
                .one()
            )
            conn.execute(
                services.update().where(services.c.id == service_id).values(deletion_requested=True)
            )
            append_event(
                conn,
                identifier,
                "queued",
                "프로젝트의 실행 자원과 전용 이미지 태그를 정리한 뒤 등록 정보를 삭제합니다.",
            )
            return {"status": "queued", "run": select_fields(row, RUN_FIELDS)}

    def snapshot(self, conn, service, settings):
        from delta_platform.adapters.onprem_removal import configured_target
        from delta_platform.core.removals import runtime_binding

        owned = list(
            conn.execute(sa.select(runs).where(runs.c.service_id == service["id"])).mappings()
        )
        deployed = [
            r
            for r in owned
            if r["kind"] == "service_deploy" and (r["submission"] or r["status"] == "succeeded")
        ]
        snapshot = {
            "service_id": str(service["id"]),
            "deployments": [],
            "runtimes": [],
            "images": [],
        }
        for r in deployed:
            snapshot["deployments"].append(
                {**r["input_snapshot"], "deployment_run_id": str(r["id"])}
            )
        deployed_ids = {r["input_snapshot"]["environment_id"] for r in deployed}
        for env in conn.execute(
            sa.select(environments).where(environments.c.service_id == service["id"])
        ).mappings():
            if str(env["id"]) in deployed_ids or env["binding"].get("runtime_status") == "removed":
                continue
            if env["cloud"] != "onprem" or not env["binding"].get("runtime"):
                raise Problem(
                    "resource_ownership_missing",
                    "이 환경에는 삭제할 자원의 소유 정보가 없습니다. 운영자가 실제 앱·VM을 연결해야 합니다.",
                    409,
                )
            target = configured_target(settings)
            runtime = runtime_binding(env)
            if (
                runtime["cluster_id"] != target["cluster_id"]
                or runtime["namespace"] not in target["namespaces"]
            ):
                raise Problem("runtime_target_changed", "등록된 온프레미스 연결을 확인하세요.", 409)
            snapshot["runtimes"].append(
                {
                    "environment_id": str(env["id"]),
                    "runtime": runtime,
                    "target": target,
                    "binding_hash": digest({"runtime": runtime, "target": target}),
                }
            )
        if deployed:
            from delta_platform.adapters.deployment import deployment_settings
            from delta_platform.adapters.jenkins import jenkins_target
            from delta_platform.core.deployment_targets import connection

            snapshot["runner"] = jenkins_target(deployment_settings(settings))
            if any(
                d["runner"] != snapshot["runner"]
                or d["connection"] != connection(settings, d["target"]["cloud"])
                for d in snapshot["deployments"]
            ):
                raise Problem(
                    "deployment_target_changed",
                    "배포 당시의 실행기·계정 연결을 복원한 뒤 삭제하세요.",
                    409,
                )
        for run in owned:
            if run["kind"] == "image_build" and (
                run["submission"] or run["external_id"] or run["status"] == "succeeded"
            ):
                target = run["input_snapshot"]["build_target"]
                snapshot["images"].append(
                    {
                        "repository": target["repository"],
                        "region": target["region"],
                        "tag": "run-" + str(run["id"]),
                    }
                )
        return snapshot
