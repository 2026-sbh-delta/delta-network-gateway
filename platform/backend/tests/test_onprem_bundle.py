from uuid import uuid4

import pytest
from test_builds import DIGEST, REPOSITORY
from test_onprem_sandbox import Runtime
from test_onprem_sandbox import config as config

from delta_platform.adapters.onprem_bundle import BackendTarget, BundleRuntime
from delta_platform.adapters.sandbox import SandboxError


def providers(config):
    front, back = Runtime(config), Runtime(config)
    back.target = BackendTarget.model_validate(back.target.model_dump())
    back.app_label = "delta-platform-sandbox-backend"
    source = {
        "build_id": str(uuid4()),
        "image": REPOSITORY + "@" + DIGEST,
        "image_digest": DIGEST,
        "healthcheck_path": "/healthz",
    }
    return BundleRuntime(front, source, backend_provider=back), front, back


def test_resume_partial_creation_and_detect_backend_image_change(config):
    runtime, front, back = providers(config)
    run, commit, image = uuid4(), "a" * 40, REPOSITORY + "@" + DIGEST
    back.create(run, commit, image, 8080, lambda: None)
    runtime.create(run, commit, image, 3000, lambda: None)
    result = runtime.verify(run, commit, image, "/healthz", lambda: None)
    assert set(result["components"]) == {"frontend", "backend"}
    assert front.creates == back.creates == 1
    back.items["pods"][back.target.name(run)]["spec"]["containers"][0]["image"] += "wrong"
    with pytest.raises(SandboxError):
        runtime.verify(run, commit, image, "/healthz", lambda: None)


def test_partial_cleanup_still_removes_backend(config):
    runtime, front, back = providers(config)
    run, image = uuid4(), REPOSITORY + "@" + DIGEST
    runtime.create(run, "a" * 40, image, 3000, lambda: None)
    front.fail_cleanup = True
    with pytest.raises(ConnectionError):
        runtime.cleanup(run, "a" * 40, image, lambda: None)
    assert not any(back.items.values())


def test_foreign_backend_never_removed(config):
    runtime, front, back = providers(config)
    run, image = uuid4(), REPOSITORY + "@" + DIGEST
    runtime.create(run, "a" * 40, image, 3000, lambda: None)
    back.items["pods"][back.target.name(run)]["metadata"]["labels"]["delta-run-id"] = str(uuid4())
    with pytest.raises(SandboxError):
        runtime.cleanup(run, "a" * 40, image, lambda: None)
    assert back.items["pods"] and not any(front.items.values())


def test_store_binds_backend_to_project_commit_and_request_key(store, data, config, engine):
    import copy

    import sqlalchemy as sa
    from test_onprem_sandbox import queued

    from delta_platform.adapters.sandbox import sandbox_target
    from delta_platform.core.errors import Problem
    from delta_platform.core.schema import runs

    old, build = queued(store, data, config)
    with engine.begin() as conn:
        conn.execute(
            runs.update()
            .where(runs.c.id == old["id"])
            .values(
                status="failed",
                error={"code": "test_fixture", "message": "fixture"},
                finished_at=sa.func.now(),
            )
        )
        other = copy.deepcopy(
            dict(conn.execute(sa.select(runs).where(runs.c.id == build["id"])).mappings().one())
        )
        other.update(id=uuid4(), request_key="backend-copy", request_hash="a" * 64)
        other["input_snapshot"]["container_port"] = 8080
        conn.execute(runs.insert().values(**other))
    target = sandbox_target(config, "onprem")
    run, _ = store.enqueue_sandbox(
        build["service_id"], build["id"], "bundle", target, backend_build_id=other["id"]
    )
    with pytest.raises(Problem) as error:
        store.enqueue_sandbox(build["service_id"], build["id"], "bundle", target)
    assert error.value.code == "idempotency_conflict"
    with engine.begin() as conn:
        conn.execute(
            runs.update()
            .where(runs.c.id == run["id"])
            .values(
                status="failed",
                error={"code": "test_fixture", "message": "fixture"},
                finished_at=sa.func.now(),
            )
        )
        conn.execute(
            runs.update().where(runs.c.id == other["id"]).values(resolved_commit_sha="e" * 40)
        )
    with pytest.raises(Problem) as error:
        store.enqueue_sandbox(
            build["service_id"], build["id"], "bundle-changed", target, backend_build_id=other["id"]
        )
    assert error.value.code == "bundle_build_mismatch"


def test_worker_persists_both_component_ids_and_cleans_the_bundle(
    store, data, config, engine, monkeypatch
):
    import copy

    import sqlalchemy as sa
    from test_onprem_sandbox import queued
    from test_worker import reader

    from delta_platform.adapters.onprem_sandbox import OnpremSandboxRunner
    from delta_platform.adapters.sandbox import sandbox_target
    from delta_platform.core.schema import runs
    from delta_platform.worker.main import process_one

    old, build = queued(store, data, config)
    with engine.begin() as conn:
        conn.execute(
            runs.update()
            .where(runs.c.id == old["id"])
            .values(
                status="failed",
                error={"code": "fixture", "message": "fixture"},
                finished_at=sa.func.now(),
            )
        )
        other = copy.deepcopy(
            dict(conn.execute(sa.select(runs).where(runs.c.id == build["id"])).mappings().one())
        )
        other.update(id=uuid4(), request_key="backend-worker", request_hash="d" * 64)
        other["input_snapshot"]["container_port"] = 8080
        conn.execute(runs.insert().values(**other))
    run, _ = store.enqueue_sandbox(
        build["service_id"],
        build["id"],
        "bundle-worker",
        sandbox_target(config, "onprem"),
        backend_build_id=other["id"],
    )
    front, back = Runtime(config), Runtime(config)
    front.token, front.transport, front.poll_seconds, front.timeout = "test", None, 0, 1
    back.target = BackendTarget.model_validate(front.target.model_dump())
    back.app_label = "delta-platform-sandbox-backend"
    monkeypatch.setattr(
        "delta_platform.adapters.onprem_bundle.BackendRuntime", lambda *args, **kwargs: back
    )
    process_one(store, reader(), sandbox=OnpremSandboxRunner(config, provider=front))
    outcome = store.run(run["id"])
    assert outcome["status"] == "succeeded"
    assert outcome["result"]["components"]["backend"]["build_id"] == str(other["id"])
    assert outcome["result"]["components"]["frontend"]["build_id"] == str(build["id"])
    assert outcome["result"]["service_id"] == str(build["service_id"])
    assert not any(front.items.values()) and not any(back.items.values())
