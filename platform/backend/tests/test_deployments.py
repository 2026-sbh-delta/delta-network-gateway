import base64
import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa
from pydantic import SecretStr, ValidationError
from support import settings
from test_builds import REPOSITORY, Provider, queued, runner
from test_worker import reader

from delta_platform.adapters.builds import BuildError
from delta_platform.adapters.deployment import DeploymentRunner
from delta_platform.core.deployment_targets import DeploymentTarget, catalog
from delta_platform.core.deployments import DeploymentInput, DeploymentStore, validate_report
from delta_platform.core.environments import EnvironmentInput, EnvironmentStore
from delta_platform.core.errors import Problem
from delta_platform.core.inputs import ServiceInput
from delta_platform.core.schema import environments, runs
from delta_platform.core.store import digest
from delta_platform.worker.main import process_one

TARGETS = [
    {"cloud": "gcp", "region": "asia-northeast3", "profile_id": "e2-micro"},
    {"cloud": "onprem", "region": "onprem-local", "profile_id": "small", "host_id": "delta-onprem"},
]


def configured():
    return settings().model_copy(
        update={
            "jenkins_url": "http://127.0.0.1:9000",
            "build_repository": REPOSITORY,
            "deployment_jenkins_user": "deployer",
            "deployment_jenkins_token": SecretStr("test-deploy-only"),
            "deployment_connections": json.dumps(
                {
                    "aws": {"revision": "v1", "account_id": "111122223333"},
                    "gcp": {"revision": "v1", "gcp_project": "term2-delta"},
                    "onprem": {"revision": "v1", "host_id": "delta-onprem"},
                }
            ),
        }
    )


def setup(engine, store, data, *, checks=True, source=True):
    build, _ = queued(store, data)
    process_one(store, reader(), runner(Provider()))
    build = store.run(build["id"])
    if checks:
        with engine.begin() as conn:
            for target in TARGETS:
                snapshot = {
                    "build_id": str(build["id"]),
                    "image": build["result"]["image"],
                    "sandbox_target": {"cloud": target["cloud"]},
                }
                conn.execute(
                    runs.insert().values(
                        id=uuid4(),
                        service_id=build["service_id"],
                        kind="sandbox_check",
                        input_snapshot=snapshot,
                        request_key=target["cloud"],
                        request_hash=digest(snapshot),
                        status="succeeded",
                        resolved_commit_sha=build["resolved_commit_sha"],
                        finished_at=datetime.now(UTC),
                        result={"runtime_check": "passed", "cleanup": "stopped"},
                    )
                )
    environment = (
        EnvironmentStore(engine).bind(
            build["service_id"],
            EnvironmentInput(
                name="existing-aws",
                cloud="aws",
                region="ap-northeast-2",
                version="old",
                account_id="111122223333",
            ),
        )
        if source
        else None
    )
    body = DeploymentInput(
        kind="migration" if source else "deployment",
        build_id=build["id"],
        source_environment_id=environment,
        targets=TARGETS if source else TARGETS[:1],
    )
    return DeploymentStore(engine, configured()), build["service_id"], body


def report(claim):
    request = claim["input_snapshot"]
    cloud = request["target"]["cloud"]
    env = {
        "name": "new-target",
        "cloud": cloud,
        "region": request["target"]["region"],
        "version": request["commit_sha"],
    }
    if cloud == "gcp":
        env.update(
            gcp_project="term2-delta",
            instances=[{"id": "12345", "name": "test-vm", "zone": "asia-northeast3-a"}],
        )
    else:
        env.update(
            instances=[{"id": "onprem-" + "a" * 32, "name": "delta-onprem"}],
            runtime={
                "cluster_id": str(uuid4()),
                "namespace": "delta-apps",
                "resources": [{"kind": "Deployment", "name": "app-test", "uid": str(uuid4())}],
            },
        )
    return {
        "schema_version": 1,
        "run_id": str(claim["id"]),
        "plan_hash": request["plan_hash"],
        "environment_id": request["environment_id"],
        "target": request["target"],
        "images": [c["image"] for c in request["components"]],
        "commit_sha": request["commit_sha"],
        "health_passed": True,
        "environment": env,
        "service_url": "http://10.1.2.3:8088",
    }


def test_registration_catalog_and_invalid_specs(store, data, signed):
    assert signed.get("/api/v1/deployment-options").status_code == 200
    assert not any(o["configured"] for o in catalog(settings())["items"])
    assert all(o["configured"] for o in catalog(configured())["items"])
    service, _ = store.register(
        data.model_copy(update={"deployment_target": DeploymentTarget(**TARGETS[1])}), "target"
    )
    assert store.service(service["id"])["deployment_target"]["host_id"] == "delta-onprem"
    for patch in ({"profile_id": "e2-highmem-64"}, {"region": "us-central1"}, {"host_id": "other"}):
        with pytest.raises(ValidationError):
            DeploymentTarget(**{**TARGETS[0], **patch})
    with pytest.raises(ValidationError):
        ServiceInput(
            **{**data.model_dump(), "deployment_target": {**TARGETS[0], "command": "evil"}}
        )


def test_double_target_admission_is_idempotent_and_serialized(engine, store, data):
    deployments, service_id, body = setup(engine, store, data)
    plan, created = deployments.create(service_id, body, "plan")
    assert created and plan["executable"]
    again, created = deployments.create(service_id, body, "plan")
    assert not created and again["id"] == plan["id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        outputs = list(
            pool.map(
                lambda _: deployments.execute(plan["id"], plan["input_hash"], "test"), range(2)
            )
        )
    assert all(p["status"] == "queued" for p in outputs)
    with engine.connect() as conn:
        assert (
            conn.scalar(
                sa.select(sa.func.count()).select_from(runs).where(runs.c.kind == "service_deploy")
            )
            == 2
        )
    with pytest.raises(Problem):
        store.enqueue(service_id, "cannot-check-during-deploy")
    with pytest.raises(Problem):
        store.delete_service(service_id, data.name)


def test_partial_failure_keeps_source_and_registers_success(engine, store, data):
    deployments, service_id, body = setup(engine, store, data)
    plan, _ = deployments.create(service_id, body, "plan")
    deployments.execute(plan["id"], plan["input_hash"], "test")

    class Deployer:
        def execute(self, claim, store, progress):
            if claim["input_snapshot"]["target"]["cloud"] == "onprem":
                raise BuildError("route_unavailable", "온프레미스 경로 연결이 필요합니다.")
            progress("deploy_verify", "verify")
            return report(claim)

    assert process_one(store, reader(), deployer=Deployer())
    assert process_one(store, reader(), deployer=Deployer())
    state = deployments.get(plan["id"])
    assert state["status"] == "partial"
    assert [t["status"] for t in state["targets"]] == ["succeeded", "failed"]
    current = EnvironmentStore(engine).list(service_id)["items"]
    assert len(current) == 2
    assert next(e for e in current if e["id"] == body.source_environment_id)["version"] == "old"
    assert sum(e["cloud"] == "gcp" for e in current) == 1
    assert not any(e["cloud"] == "onprem" for e in current)
    assert deployments.execute(plan["id"], plan["input_hash"], "test")["status"] == "partial"


def test_missing_checks_block_execution_not_planning(engine, store, data):
    deployments, service_id, body = setup(engine, store, data, checks=False)
    plan, _ = deployments.create(service_id, body, "plan")
    assert not plan["executable"] and all("샌드박스" in t["reason"] for t in plan["targets"])
    with pytest.raises(Problem) as error:
        deployments.execute(plan["id"], plan["input_hash"], "test")
    assert error.value.code == "deployment_not_ready"


def test_stale_source_hash_and_foreign_build_rejected(engine, store, data):
    deployments, service_id, body = setup(engine, store, data)
    with pytest.raises(Problem):
        deployments.create(service_id, body.model_copy(update={"build_id": uuid4()}), "bad")
    plan, _ = deployments.create(service_id, body, "plan")
    with pytest.raises(Problem) as error:
        deployments.execute(plan["id"], "0" * 64, "test")
    assert error.value.code == "approval_mismatch"
    with engine.begin() as conn:
        conn.execute(
            environments.update()
            .where(environments.c.id == body.source_environment_id)
            .values(version="changed")
        )
    with pytest.raises(Problem) as error:
        deployments.execute(plan["id"], plan["input_hash"], "test")
    assert error.value.code == "source_changed"


def test_source_target_and_initial_count_rejected(engine, store, data):
    deployments, service_id, body = setup(engine, store, data)
    with pytest.raises(ValidationError):
        DeploymentInput(kind="deployment", build_id=body.build_id, targets=TARGETS)
    with pytest.raises(ValidationError):
        DeploymentInput(
            kind="migration",
            build_id=body.build_id,
            source_environment_id=body.source_environment_id,
            targets=[TARGETS[0], TARGETS[0]],
        )
    with pytest.raises(Problem) as error:
        deployments.create(
            service_id,
            body.model_copy(
                update={
                    "targets": [
                        DeploymentTarget(
                            cloud="aws", region="ap-northeast-2", profile_id="t3.micro"
                        )
                    ]
                }
            ),
            "bad",
        )
    assert error.value.code == "source_is_target"


def test_invalid_report_never_registers_environment(engine, store, data):
    deployments, service_id, body = setup(engine, store, data, source=False)
    plan, _ = deployments.create(service_id, body, "plan")
    deployments.execute(plan["id"], plan["input_hash"], "test")
    claim = store.claim()
    result = report(claim)
    for patch in (
        {"health_passed": False},
        {"environment_id": str(uuid4())},
        {"images": []},
        {"service_url": "javascript:alert(1)"},
    ):
        with pytest.raises(ValueError):
            validate_report(claim, {**result, **patch})
    with pytest.raises(ValueError):
        store.finish(claim["id"], claim["lease_token"], result={**result, "health_passed": False})
    assert EnvironmentStore(engine).list(service_id)["items"] == []
    store.finish(claim["id"], claim["lease_token"], result=result)
    assert deployments.get(plan["id"])["status"] == "succeeded"
    assert len(EnvironmentStore(engine).list(service_id)["items"]) == 1


def test_jenkins_recovers_submission_without_duplicate_post(engine, store, data):
    deployments, service_id, body = setup(engine, store, data, source=False)
    plan, _ = deployments.create(service_id, body, "plan")
    deployments.execute(plan["id"], plan["input_hash"], "test")
    claim = store.claim()
    adapter = DeploymentRunner(configured(), sleep=lambda _: None)
    params = adapter.parameters(claim)
    payload = json.loads(base64.b64decode(params["DELTA_DEPLOYMENT"]))
    assert "runner" not in payload and "token" not in payload
    store.prepare_submission(claim["id"], claim["lease_token"], params)
    claim["submission"] = {"request": params}
    calls = []

    def handler(request):
        calls.append(request.method)
        item = {
            "number": 1,
            "actions": [{"parameters": [{"name": k, "value": v} for k, v in params.items()]}],
            "building": False,
            "result": "SUCCESS",
        }
        if request.url.path.endswith("/artifact/result.json"):
            return httpx.Response(200, json=report(claim))
        if request.url.path.endswith("/1/api/json"):
            return httpx.Response(200, json=item)
        return httpx.Response(200, json={"builds": [item]})

    adapter.transport = httpx.MockTransport(handler)
    assert adapter.execute(claim, store, lambda *args: None)["health_passed"]
    assert calls and set(calls) == {"GET"}


def test_onprem_renderer_preserves_removal_contract():
    path = Path(__file__).resolve().parents[2] / "deploy/workloads/deploy.py"
    spec = importlib.util.spec_from_file_location("deployment_executor", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = {
        "environment_id": str(uuid4()),
        "service_id": str(uuid4()),
        "plan_hash": "a" * 64,
        "target": TARGETS[1],
        "components": [
            {
                "name": name,
                "image": REPOSITORY + "@sha256:" + "b" * 64,
                "container_port": port,
                "healthcheck_path": "/health",
            }
            for name, port in (("app", 3000), ("backend", 8080))
        ],
    }
    objects = module.k3s_objects(
        data,
        {
            "namespace": "delta-apps",
            "image_pull_secret": "ecr-pull",
            "ingress_cidrs": ["10.80.1.70/32"],
        },
    )
    apps = [o for o in objects if o["kind"] == "Deployment"]
    assert len(apps) == 2 and all(
        len(a["spec"]["template"]["spec"]["containers"]) == 1 for a in apps
    )
    assert (
        sum(
            int(
                a["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"][
                    "memory"
                ].removesuffix("Mi")
            )
            for a in apps
        )
        == 512
    )
    assert all(a["spec"]["template"]["spec"]["automountServiceAccountToken"] is False for a in apps)
    assert sum(o["spec"].get("type") == "NodePort" for o in objects) == 1


def test_deployment_api_requires_auth_and_plan_hash(engine, store, data):
    from fastapi.testclient import TestClient
    from support import LOGIN_ID, PASSWORD

    from delta_platform.api.app import create_app

    _, service_id, body = setup(engine, store, data, source=False)
    with TestClient(create_app(configured(), engine), base_url="http://127.0.0.1:8088") as client:
        path = f"/api/v1/services/{service_id}/deployment-plans"
        assert client.get(path).status_code == 401
        client.headers.update({"Origin": "http://127.0.0.1:8088", "X-Platform-Request": "1"})
        login = client.post("/api/v1/auth/login", json={"login_id": LOGIN_ID, "password": PASSWORD})
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        response = client.post(
            path, json=body.model_dump(mode="json"), headers={"Idempotency-Key": "http-plan"}
        )
        assert response.status_code == 201
        plan = response.json()
        assert (
            plan["executable"] and plan["input_snapshot"]["targets"][0]["profile_id"] == "e2-micro"
        )
        execute = f"/api/v1/deployment-plans/{plan['id']}/execute"
        assert client.post(execute, json={"input_hash": "0" * 64}).status_code == 409
        assert client.post(execute, json={"input_hash": plan["input_hash"]}).status_code == 200
        current = client.get(f"/api/v1/deployment-plans/{plan['id']}").json()
        assert current["status"] == "queued"
        assert (
            client.get(
                f"/api/v1/deployment-runs/{current['targets'][0]['run']['id']}/events"
            ).status_code
            == 200
        )


def test_executor_never_applies_delete_or_replacement(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "deploy/workloads/deploy.py"
    spec = importlib.util.spec_from_file_location("deployment_executor", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    def command(args, **kwargs):
        calls.append(args)
        return (
            json.dumps({"resource_changes": [{"change": {"actions": ["delete", "create"]}}]})
            if args[1] == "show"
            else ""
        )

    monkeypatch.setattr(module, "run", command)
    data = {
        "environment_id": str(uuid4()),
        "service_id": str(uuid4()),
        "target": TARGETS[0],
        "connection": {"gcp_project": "term2-delta"},
    }
    with pytest.raises(ValueError, match="must not update/delete"):
        module.provision(data, {"terraform": {"project_id": "term2-delta"}}, tmp_path)
    assert not any(c[1] == "apply" for c in calls)
