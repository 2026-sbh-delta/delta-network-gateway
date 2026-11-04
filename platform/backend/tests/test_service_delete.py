from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
import sqlalchemy as sa

from delta_platform.core.environments import EnvironmentInput, EnvironmentStore
from delta_platform.core.errors import Problem
from delta_platform.core.schema import environments, events, runs, services, workflows


def register(store, data, name=None):
    return store.register(data.model_copy(update={"name": name}) if name else data, str(uuid4()))[0]


def remove(client, service, **body):
    return client.post(
        f"/api/v1/services/{service['id']}/delete",
        json={"confirmation_name": service["name"], **body},
    )


def finish(conn, run_id):
    conn.execute(
        runs.update()
        .where(runs.c.id == run_id)
        .values(
            status="failed",
            finished_at=sa.func.now(),
            error={"code": "test", "message": "completed failure"},
        )
    )


def test_delete_only_owns_finished_history_and_is_retry_safe(signed, store, data, engine):
    first, other = register(store, data), register(store, data, "keep-project")
    first_run = store.enqueue(first["id"], "first")[0]
    other_run = store.enqueue(other["id"], "other")[0]
    with engine.begin() as conn:
        finish(conn, first_run["id"])
        finish(conn, other_run["id"])
        assert conn.scalar(sa.select(sa.func.count()).select_from(events)) > 0
    assert remove(signed, first).status_code == 204
    assert remove(signed, first).status_code == 204
    assert signed.get(f"/api/v1/services/{first['id']}").status_code == 404
    assert signed.get(f"/api/v1/services/{other['id']}").status_code == 200
    assert signed.get("/api/v1/services").json()["items"][0]["id"] == str(other["id"])
    with engine.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(runs)) == 1
        assert conn.scalar(sa.select(runs.c.id)) == other_run["id"]
        assert (
            conn.scalar(
                sa.select(sa.func.count())
                .select_from(events)
                .where(events.c.run_id == first_run["id"])
            )
            == 0
        )
        assert (
            conn.scalar(
                sa.select(sa.func.count())
                .select_from(events)
                .where(events.c.run_id == other_run["id"])
            )
            > 0
        )


def test_delete_requires_login(client, store, data):
    service = register(store, data)
    assert remove(client, service).status_code == 401
    assert store.service(service["id"])["id"] == service["id"]


def test_delete_requires_csrf_origin_and_exact_name(signed, store, data):
    service = register(store, data)
    csrf = signed.headers.pop("X-CSRF-Token")
    assert remove(signed, service).status_code == 403
    signed.headers["X-CSRF-Token"] = csrf
    origin = signed.headers["Origin"]
    signed.headers["Origin"] = "https://untrusted.example"
    assert remove(signed, service).status_code == 403
    signed.headers["Origin"] = origin
    assert (
        remove(signed, service, confirmation_name="wrong").json()["error"]["code"]
        == "delete_name_mismatch"
    )
    assert remove(signed, service, unexpected=True).status_code == 422
    assert store.service(service["id"])["name"] == service["name"]


@pytest.mark.parametrize("running", [False, True])
def test_delete_refuses_active_and_cleanup_pending_work(signed, store, data, engine, running):
    service = register(store, data)
    run = store.enqueue(service["id"], "active")[0]
    if running:
        with engine.begin() as conn:
            conn.execute(
                runs.update()
                .where(runs.c.id == run["id"])
                .values(
                    status="running",
                    lease_token=uuid4(),
                    lease_expires_at=sa.func.now() + sa.text("interval '1 minute'"),
                    current_step="dast_cleanup_wait",
                    cancel_requested=True,
                )
            )
    response = remove(signed, service)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "service_busy"
    assert store.run(run["id"])["id"] == run["id"]


@pytest.mark.parametrize("dependency", ["environment", "workflow"])
def test_delete_refuses_environment_or_plan_and_preserves_records(
    signed, store, data, engine, dependency
):
    service = register(store, data)
    if dependency == "environment":
        EnvironmentStore(engine).bind(
            service["id"],
            EnvironmentInput(
                name="production", cloud="aws", region="ap-northeast-2", account_id="123456789012"
            ),
        )
    else:
        with engine.begin() as conn:
            conn.execute(
                workflows.insert().values(
                    id=uuid4(),
                    project_id=service["project_id"],
                    service_id=service["id"],
                    kind="deployment",
                    status="planned",
                    input_snapshot={},
                    input_hash="a" * 64,
                    request_key="existing-plan",
                )
            )
    response = remove(signed, service)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "service_has_dependencies"
    with engine.connect() as conn:
        table = environments if dependency == "environment" else workflows
        assert conn.scalar(sa.select(sa.func.count()).select_from(table)) == 1
        assert conn.scalar(sa.select(sa.func.count()).select_from(services)) == 1


def test_delete_serializes_with_new_run_admission(store, data):
    service = register(store, data)
    barrier = Barrier(2)

    def delete():
        barrier.wait()
        try:
            store.delete_service(service["id"], service["name"])
            return "deleted"
        except Problem as error:
            return error.code

    def enqueue():
        barrier.wait()
        try:
            store.enqueue(service["id"], "racing-run")
            return "queued"
        except Problem as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        deleted, queued = pool.submit(delete), pool.submit(enqueue)
        outcome = deleted.result(timeout=10), queued.result(timeout=10)
    assert outcome in [("deleted", "service_not_found"), ("service_busy", "queued")]
