import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from support import LOGIN_ID, PASSWORD
from test_deployments import configured

from delta_platform.api.app import create_app
from delta_platform.core.applications import (
    STEPS,
    ApplicationInput,
    ApplicationStore,
    database_catalog,
)
from delta_platform.core.errors import Problem
from delta_platform.core.schema import environments, runs, services
from delta_platform.core.store import Store, digest

SHA = "a" * 40
IMAGE = "111122223333.dkr.ecr.ap-northeast-2.amazonaws.com/app@sha256:" + "b" * 64
PROFILE = {
    "id": "demo-postgres",
    "label": "시연 PostgreSQL",
    "revision": "v1",
    "engine": "postgres",
    "clouds": ["aws", "gcp", "onprem"],
    "modes": ["new", "existing"],
}


def config():
    return configured().model_copy(update={"application_databases": json.dumps([PROFILE])})


def body(**changes):
    return ApplicationInput(
        name="Delta Notes",
        repository_url="https://github.com/example/app",
        deployment_target={"cloud": "aws", "region": "ap-northeast-2", "profile_id": "t3.small"},
        database={"profile_id": "demo-postgres", "mode": "new"},
        **changes,
    )


def finish_next(store, *, skipped=False, fail=False):
    claim = store.claim()
    if not claim:
        return None
    run_id, token, source = claim["id"], claim["lease_token"], claim["input_snapshot"]
    store.progress(run_id, token, "test", "테스트 실행", SHA)
    if fail:
        store.finish(run_id, token, error={"code": "failure", "message": "빌드 실패"})
        return claim
    result = {"commit_sha": SHA}
    if claim["kind"] == "image_build":
        result.update(image=IMAGE, image_digest="sha256:" + "b" * 64, platform="linux/amd64")
    elif claim["kind"] == "security_check":
        result.update(
            build_id=source["build_id"],
            image_digest=source["image_digest"],
            evidence_sha256="c" * 64,
            requires_review=False,
        )
    elif claim["kind"] in ("sandbox_check", "dast_check"):
        result.update(runtime_check="passed", cleanup="stopped")
        if claim["kind"] == "dast_check":
            result.update(dast_check="not_run" if skipped else "completed", requires_review=skipped)
    elif claim["kind"] == "service_deploy":
        result = {
            "schema_version": 1,
            "run_id": str(run_id),
            "plan_hash": source["plan_hash"],
            "environment_id": source["environment_id"],
            "target": source["target"],
            "images": [c["image"] for c in source["components"]],
            "commit_sha": SHA,
            "health_passed": True,
            "service_url": "http://10.10.1.2:8088",
            "database": {**source["database"], "connected": True, "retention": "retain"},
            "environment": {
                "name": "test",
                "cloud": "aws",
                "region": "ap-northeast-2",
                "version": SHA,
                "account_id": "111122223333",
                "instances": [{"id": "i-0123456789abcdef0", "name": "app"}],
            },
        }
    store.finish(run_id, token, result=result)
    return claim


@pytest.fixture
def targets(monkeypatch):
    import delta_platform.core.applications as module

    monkeypatch.setattr(
        module,
        "build_target",
        lambda _: {
            "provider": "jenkins",
            "repository": IMAGE.split("@")[0],
            "region": "ap-northeast-2",
        },
    )
    monkeypatch.setattr(module, "security_target", lambda _: {"url": "https://scanner.test"})
    monkeypatch.setattr(module, "sandbox_target", lambda _, cloud: {"cloud": cloud})
    monkeypatch.setattr(module, "dast_target", lambda _: {"url": "https://dast.test"})


def test_atomic_registration_hides_components_and_reuses_request(engine):
    apps = ApplicationStore(engine, config())
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: apps.register(body(), "one-app", "team"), range(3)))
    assert sum(created for _, created in results) == 1
    assert len({service["id"] for service, _ in results}) == 1
    service = results[0][0]
    assert len(Store(engine).list_services(50, None)["items"]) == 1
    with engine.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(services)) == 2
    with pytest.raises(Problem, match="같은 요청"):
        apps.register(body(source_ref="different"), "one-app", "team")
    child = UUID(service["application"]["components"]["backend"])
    with pytest.raises(Problem, match="함께 삭제"):
        Store(engine).delete_service(child, Store(engine).service(child)["name"], config())


def test_automatic_sequence_survives_restart_pins_sha_and_deploys_one_pair(engine, targets):
    app, _ = ApplicationStore(engine, config()).register(body(), "full", "team")
    store, seen = Store(engine), []
    for _ in range(50):
        ApplicationStore(engine, config()).tick()
        claim = finish_next(store)
        if claim:
            seen.append(claim)
    state = store.service(app["id"])["application"]
    assert state["status"] == "succeeded", state
    assert len(seen) == len(STEPS) == 11
    assert seen[1]["input_snapshot"]["source_ref"] == SHA
    deployment = seen[-1]["input_snapshot"]
    assert len(deployment["components"]) == 2
    assert {c["commit_sha"] for c in deployment["components"]} == {SHA}
    assert deployment["database"]["mode"] == "new"
    with engine.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(environments)) == 1


def test_skipped_dast_requires_explicit_review(engine, targets):
    apps, store = ApplicationStore(engine, config()), Store(engine)
    app, _ = apps.register(body(), "review", "team")
    for _ in range(40):
        apps.tick()
        finish_next(store, skipped=True)
    state = store.service(app["id"])["application"]
    assert state["status"] == "review"
    assert any(s["status"] == "skipped" for s in state["steps"])
    with pytest.raises(Problem, match="검토"):
        apps.resume(app["id"])
    apps.resume(app["id"], approve=True)
    for _ in range(5):
        apps.tick()
        finish_next(store)
    assert store.service(app["id"])["application"]["status"] == "succeeded"


def test_failed_run_never_deploys(engine, targets):
    apps, store = ApplicationStore(engine, config()), Store(engine)
    app, _ = apps.register(body(), "fail", "team")
    apps.tick()
    finish_next(store, fail=True)
    for _ in range(3):
        apps.tick()
    assert store.service(app["id"])["application"]["status"] == "failed"
    with engine.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(runs)) == 1


def test_application_api_and_catalog_do_not_expose_secrets(engine):
    cfg = config().model_copy(
        update={
            "application_databases": json.dumps([{**PROFILE, "secret_id": "never-return-this"}])
        }
    )
    assert "never-return-this" not in json.dumps(database_catalog(cfg))
    with TestClient(create_app(cfg, engine), base_url="http://127.0.0.1:8088") as client:
        client.headers.update({"Origin": cfg.public_origin, "X-Platform-Request": "1"})
        auth = client.post("/api/v1/auth/login", json={"login_id": LOGIN_ID, "password": PASSWORD})
        client.headers["X-CSRF-Token"] = auth.json()["csrf_token"]
        reply = client.post(
            "/api/v1/applications",
            json=body().model_dump(mode="json"),
            headers={"Idempotency-Key": "api"},
        )
        assert reply.status_code == 201, reply.text
        assert reply.json()["application"]["status"] == "queued"
        assert "never-return-this" not in reply.text


def test_application_delete_includes_both_images_and_retains_until_ack(engine, targets):
    from delta_platform.core.project_removal import ProjectRemovalStore

    apps, store = ApplicationStore(engine, config()), Store(engine)
    app, _ = apps.register(body(), "delete", "team")
    for _ in range(8):
        apps.tick()
        finish_next(store)
    state = store.service(app["id"])["application"]
    state["status"] = "blocked"
    apps._save(app["id"], state)
    removal = ProjectRemovalStore(engine).enqueue(app["id"], app["name"], config())
    claim = store.claim()
    assert claim["id"] == removal["run"]["id"]
    assert len(claim["input_snapshot"]["images"]) == 2
    store.finish(
        claim["id"],
        claim["lease_token"],
        result={"cleanup": "complete", "snapshot_hash": digest(claim["input_snapshot"])},
    )
    with engine.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(services)) == 0


def test_compose_keeps_two_images_private_backend_and_secret_out_of_config():
    path = Path(__file__).parents[2] / "deploy/workloads/vm_runtime.py"
    spec = importlib.util.spec_from_file_location("application_vm", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    payload = {
        "components": [
            {"name": "app", "container_port": 3000, "healthcheck_path": "/healthz"},
            {"name": "backend", "container_port": 8080, "healthcheck_path": "/healthz"},
        ],
        "images": [IMAGE, IMAGE],
        "memory_mb": 1600,
        "database_secret": {"provider": "aws", "secret_id": "private"},
    }
    compose = module.compose_document(payload)
    frontend, backend = compose["services"]["frontend"], compose["services"]["backend"]
    assert frontend["environment"]["API_HOST"] == "backend"
    assert frontend["depends_on"]["backend"]["condition"] == "service_healthy"
    assert backend["ports"] == ["127.0.0.1:8089:8080"]
    assert backend["env_file"][0]["format"] == "raw"
    assert "database.env" not in json.dumps(frontend)
    assert "private" not in json.dumps(compose)


def test_database_plan_requires_connection_evidence():
    from uuid import uuid4

    from delta_platform.core.deployments import validate_report

    # Even a healthy frontend cannot mark the DB integration complete without evidence.
    claim = {
        "id": uuid4(),
        "input_snapshot": {
            "plan_hash": "a" * 64,
            "environment_id": str(uuid4()),
            "target": {"cloud": "aws"},
            "components": [],
            "commit_sha": SHA,
            "database": {
                "profile_id": "demo-postgres",
                "revision": "v1",
                "engine": "postgres",
                "mode": "new",
            },
        },
    }
    report = {
        "schema_version": 1,
        "run_id": str(claim["id"]),
        "plan_hash": claim["input_snapshot"]["plan_hash"],
        "environment_id": claim["input_snapshot"]["environment_id"],
        "target": {"cloud": "aws"},
        "images": [],
        "commit_sha": SHA,
        "health_passed": True,
    }
    with pytest.raises(ValueError, match="Database connection"):
        validate_report(claim, report)


def test_database_credentials_are_read_on_host_not_embedded_in_compose(tmp_path, monkeypatch):
    path = Path(__file__).parents[2] / "deploy/workloads/vm_runtime.py"
    spec = importlib.util.spec_from_file_location("application_secret_vm", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    seen = []
    value = {
        "host": "database.internal",
        "dbname": "app",
        "username": "app",
        "password": "secret$literal",
        "ca_pem": "TEST CA",
    }

    def command(args):
        seen.append(args)
        return json.dumps({"SecretString": json.dumps(value)})

    monkeypatch.setattr(module, "command", command)
    module.write_database_secret(tmp_path, {"provider": "aws", "secret_id": "owned-db"})
    assert "secret$literal" not in json.dumps(seen)
    env = tmp_path / "database.env"
    assert env.stat().st_mode & 0o777 == 0o600
    assert "PGPASSWORD=secret$literal\n" in env.read_text()
    assert "STORAGE_BACKEND=postgres" in env.read_text()
    value["password"] = "invalid\nINJECTED=true"
    with pytest.raises(ValueError):
        module.write_database_secret(tmp_path, {"provider": "aws", "secret_id": "owned-db"})


def test_application_migration_cannot_silently_move_frontend_only(engine, targets):
    from uuid import uuid4

    from delta_platform.core.deployments import DeploymentInput, DeploymentStore

    app, _ = ApplicationStore(engine, config()).register(body(), "migration", "team")
    with pytest.raises(Problem, match="DB 이전 계획"):
        DeploymentStore(engine, config()).create(
            app["id"],
            DeploymentInput(
                kind="migration",
                build_id=uuid4(),
                source_environment_id=uuid4(),
                targets=[{"cloud": "gcp", "region": "asia-northeast3", "profile_id": "e2-small"}],
            ),
            "move",
        )
