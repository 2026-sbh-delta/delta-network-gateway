from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from pydantic import ValidationError
from support import LOGIN_ID, PASSWORD, settings
from test_workflows import setup_plan

from delta_platform.api.app import create_app
from delta_platform.core.errors import Problem
from delta_platform.core.schema import workflow_executions
from delta_platform.core.store import digest
from delta_platform.core.workflows import WorkflowInput
from delta_platform.migration.cli import execute
from delta_platform.migration.postgres import Journal, MigrationError
from delta_platform.migration.tracking import MigrationTrackingStore


def setup_migration(engine, store, data, *, approved=True):
    _, service, source, target, body = setup_plan(engine, store, data)
    db_schema = {"tables": [{"name": "notes"}], "sequences": []}
    plan = {
        "version": 1,
        "source_environment_id": str(source),
        "target_environment_id": str(target),
        "source": {
            "system_id": "111",
            "database": "customer",
            "major_version": 17,
            "operator": "operator",
        },
        "target": {
            "system_id": "222",
            "database": "customer",
            "major_version": 17,
            "operator": "operator",
        },
        "schema": db_schema,
        "schema_hash": digest(db_schema),
        "writer_roles": ["app"],
        "replication_role": "replicator",
    }
    tracking = MigrationTrackingStore(engine)
    wf, _ = tracking.register(service, body.build_id, plan, "move-plan")
    if approved:
        tracking.approve(wf["id"], wf["input_hash"], "operator")
    return tracking, service, source, target, wf, plan


def test_tracking_requires_exact_approved_plan_and_preserves_execution_id(engine, store, data):
    tracking, _, source, target, wf, plan = setup_migration(engine, store, data, approved=False)
    with pytest.raises(Problem, match="승인"):
        tracking.prepare(wf["id"], plan)
    with engine.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(workflow_executions)) == 0
    tracking.approve(wf["id"], wf["input_hash"], "operator")
    context = tracking.prepare(wf["id"], plan)
    assert context == tracking.prepare(wf["id"], plan)
    assert context["source_environment_id"] == str(source) and context["environment_id"] == str(
        target
    )
    with pytest.raises(Problem, match="출발"):
        tracking.prepare(wf["id"], {**plan, "source_environment_id": str(uuid4())})
    with pytest.raises(Problem, match="승인한 계획"):
        tracking.prepare(wf["id"], {**plan, "writer_roles": ["other"]})
    with pytest.raises(Problem, match="형식"):
        tracking.prepare(wf["id"], {**plan, "connection_dsn": "do-not-store"})


def test_latest_owned_progress_no_raw_inputs_stale_and_terminal_state(engine, store, data):
    tracking, service, source, target, wf, plan = setup_migration(engine, store, data)
    ctx = tracking.prepare(wf["id"], plan)
    tracking.record(ctx, {"phase": "copying", "lag_bytes": 1024})
    result = tracking.latest(service, target)
    assert result["stage"] == "copying" and result["lag_bytes"] == 1024
    assert result["validation"] == "pending" and result["status"] == "ok"
    assert not {"payload", "schema", "writer_roles", "source"}.intersection(result)
    assert tracking.latest(uuid4(), target) is None and tracking.latest(service, source) is None
    future = datetime.now(UTC) + timedelta(minutes=6)
    assert tracking.latest(service, target, now=future)["status"] == "stale"
    tracking.record(ctx, {"phase": "source_fenced"}, blocked=True)
    assert tracking.latest(service, target, now=future)["status"] == "blocked"
    with pytest.raises(Problem, match="단계"):
        tracking.record(ctx, {"phase": "copying"})
    with pytest.raises(Problem, match="DB 검증"):
        tracking.record(ctx, {"phase": "cleaned"})
    tracking.record(ctx, {"phase": "cleaned", "validated": True})
    final = tracking.latest(service, target)
    tracking.record(ctx, {"phase": "cleaned", "validated": True})
    assert tracking.latest(service, target) == final
    assert tracking.latest(service, target, now=future)["status"] == "completed"
    assert tracking.get(wf["id"])["status"] == "running"  # App/traffic steps are separate.
    with pytest.raises(Problem, match="완료된"):
        tracking.record(ctx, {"phase": "aborted"})
    with pytest.raises(ValidationError):
        tracking.record(ctx, {"phase": "cleaned", "validated": True, "lag_bytes": -1})
    # A new prepared run must not keep displaying an older run's completion.
    new_workflow, _ = tracking.create(
        service,
        WorkflowInput(
            build_id=wf["input_snapshot"]["build_id"],
            source_environment_id=source,
            target_environment_ids=[target],
            actions=wf["input_snapshot"]["actions"],
        ),
        "second-plan",
    )
    tracking.approve(new_workflow["id"], new_workflow["input_hash"], "operator")
    new_context = tracking.prepare(new_workflow["id"], plan)
    prepared = tracking.latest(service, target)
    assert prepared["run_id"] == new_context["run_id"] and prepared["stage"] == "planned"
    assert prepared["lag_bytes"] is None and prepared["validation"] == "pending"


def test_context_swap_cannot_record_and_pending_promotion_cannot_be_aborted(engine, store, data):
    tracking, _, _, _, wf, plan = setup_migration(engine, store, data)
    ctx = tracking.prepare(wf["id"], plan)
    with pytest.raises(Problem, match="소유 관계"):
        tracking.record({**ctx, "service_id": str(uuid4())}, {"phase": "copying"})
    tracking.record(ctx, {"phase": "promotion_pending", "validated": True})
    with pytest.raises(Problem, match="단계"):
        tracking.record(ctx, {"phase": "aborted", "validated": True})


def test_driver_failure_stays_blocked_then_same_journal_can_resume(engine, store, data, tmp_path):
    tracking, service, _, target, wf, plan = setup_migration(engine, store, data)
    ctx = tracking.prepare(wf["id"], plan)
    with Journal(
        tmp_path / "journal.json", plan, workflow_id=wf["id"], run_id=ctx["run_id"]
    ) as journal:

        class FailingDriver:
            def start(self, journal, approved_hash):
                journal.save("copying")
                raise MigrationError("제어된 DB 실패")

        with pytest.raises(MigrationError):
            execute(FailingDriver(), journal, "start", digest(plan), tracker=tracking, context=ctx)
        result = tracking.latest(service, target)
        assert result["status"] == "blocked" and result["stage"] == "copying"

        class StatusDriver:
            def status(self, journal):
                return {"phase": "copying", "lag_bytes": 17}

        execute(StatusDriver(), journal, "status", None, tracker=tracking, context=ctx)
        assert tracking.latest(service, target)["status"] == "ok"
        assert tracking.latest(service, target)["lag_bytes"] == 17


def test_dashboard_reads_only_selected_service_environment_and_keeps_other_fields(
    engine, store, data
):
    tracking, service, _, target, wf, plan = setup_migration(engine, store, data)
    ctx = tracking.prepare(wf["id"], plan)
    tracking.record(ctx, {"phase": "synchronizing", "lag_bytes": 34})

    class Dashboard:
        def read(self, env, minutes):
            return {"migration": None, "metrics": {"placeholder": "test-only"}}

    with TestClient(
        create_app(settings(), engine, dashboard=Dashboard()), base_url="http://127.0.0.1:8088"
    ) as client:
        client.headers.update({"Origin": "http://127.0.0.1:8088", "X-Platform-Request": "1"})
        path = f"/api/v1/services/{service}/observability?environment_id={target}"
        assert client.get(path).status_code == 401
        login = client.post("/api/v1/auth/login", json={"login_id": LOGIN_ID, "password": PASSWORD})
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        result = client.get(path).json()
        assert result["migration"]["run_id"] == ctx["run_id"]
        assert result["migration"]["lag_bytes"] == 34
        assert (
            client.get(
                f"/api/v1/services/{service}/observability?environment_id={uuid4()}"
            ).status_code
            == 404
        )
    assert UUID(ctx["workflow_id"]) == wf["id"]
