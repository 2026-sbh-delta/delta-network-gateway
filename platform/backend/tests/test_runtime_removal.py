import base64
import copy
import json
import ssl
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from support import settings
from test_worker import expire, reader

from delta_platform.adapters.onprem_removal import OnpremRemover, configured_target
from delta_platform.adapters.sandbox import SandboxError
from delta_platform.core.environments import EnvironmentInput, EnvironmentStore
from delta_platform.core.errors import LeaseLost, Problem
from delta_platform.core.removals import RemovalStore
from delta_platform.core.runtime import OnpremRuntime
from delta_platform.worker.main import process_one


@pytest.fixture
def setup_runtime(store, data, engine, monkeypatch):
    service = store.register(data, "runtime-service")[0]
    cert = ssl.DER_cert_to_PEM_cert(ssl.create_default_context().get_ca_certs(binary_form=True)[0])
    cluster = str(uuid4())
    cfg = settings().model_copy(
        update={
            "onprem_runtime_target": json.dumps(
                {
                    "cluster_id": cluster,
                    "api_url": "https://10.40.0.10:6443",
                    "ca_base64": base64.b64encode(cert.encode()).decode(),
                    "namespaces": ["delta-test"],
                }
            ),
            "onprem_runtime_token": SecretStr("test-only-runtime-token"),
        }
    )
    target = configured_target(cfg)
    monkeypatch.setattr(
        "delta_platform.adapters.onprem_removal.configured_target", lambda _: target
    )
    runtime = OnpremRuntime.model_validate(
        {
            "cluster_id": cluster,
            "namespace": "delta-test",
            "resources": [
                {"kind": "Deployment", "name": "app", "uid": str(uuid4())},
                {"kind": "Service", "name": "app", "uid": str(uuid4())},
            ],
        }
    )
    envs = EnvironmentStore(engine)
    body = EnvironmentInput(
        name="onprem-test", cloud="onprem", region="onprem-lab", runtime=runtime
    )
    identifier = envs.bind(service["id"], body)
    env = envs.get(service["id"], identifier)
    removes = RemovalStore(engine)
    plan = removes.preview(env, target)
    items = {}
    for r in runtime.resources:
        collection = "deployments" if r.kind == "Deployment" else "services"
        items[collection] = {
            "kind": r.kind,
            "metadata": {
                "uid": str(r.uid),
                "name": r.name,
                "namespace": runtime.namespace,
                "resourceVersion": "7",
                "labels": {
                    "app.kubernetes.io/managed-by": "delta-platform",
                    "delta-platform/service-id": str(service["id"]),
                    "delta-platform/environment-id": str(identifier),
                },
            },
        }
    items["deployments"]["spec"] = {"template": {"spec": {"containers": [{"name": "app"}]}}}
    return {
        "service": service,
        "environment": env,
        "config": cfg,
        "target": target,
        "body": body,
        "store": removes,
        "envs": envs,
        "plan": plan,
        "items": items,
        "calls": [],
    }


def path(value):
    return f"/api/v1/services/{value['service']['id']}/environments/{value['environment']['id']}/removal"


def enqueue(value, key="delete-1"):
    return value["store"].enqueue(
        value["service"]["id"],
        value["environment"]["id"],
        value["service"]["name"],
        value["plan"]["binding_hash"],
        key,
        value["target"],
    )[0]


def remover(value, *, fail_after_one=False, pending=False):
    def send(request):
        collection = request.url.path.split("/")[-2]
        assert collection in ("deployments", "services")
        assert "/namespaces/delta-test/" in request.url.path
        value["calls"].append((request.method, collection))
        item = value["items"].get(collection)
        if item is None:
            return httpx.Response(404)
        if request.method == "DELETE":
            if fail_after_one and collection == "deployments":
                return httpx.Response(503, text="private token must not be exposed")
            body = json.loads(request.content)
            assert body["propagationPolicy"] == "Foreground"
            assert body["preconditions"] == {"uid": item["metadata"]["uid"], "resourceVersion": "7"}
            if not pending:
                del value["items"][collection]
        return httpx.Response(200, json=item)

    return OnpremRemover(
        value["config"], transport=httpx.MockTransport(send), timeout=0, poll_seconds=0
    )


def test_api_confirmation_csrf_stale_plan_and_foreign_environment(signed, setup_runtime):
    value = setup_runtime
    preview = signed.get(path(value)).json()
    assert preview["configured"] and len(preview["resources"]) == 2
    assert "ca_base64" not in json.dumps(preview)
    body = {"confirmation_name": value["service"]["name"], "binding_hash": preview["binding_hash"]}
    headers = {"Idempotency-Key": "delete-api"}
    token = signed.headers.pop("X-CSRF-Token")
    assert signed.post(path(value), json=body, headers=headers).status_code == 403
    signed.headers["X-CSRF-Token"] = token
    assert (
        signed.post(
            path(value), json={**body, "confirmation_name": "wrong"}, headers=headers
        ).status_code
        == 409
    )
    assert (
        signed.post(
            path(value), json={**body, "binding_hash": "a" * 64}, headers=headers
        ).status_code
        == 409
    )
    assert (
        signed.post(path(value), json={**body, "namespace": "demo"}, headers=headers).status_code
        == 422
    )
    assert (
        signed.post(
            path(value).replace(str(value["environment"]["id"]), str(uuid4())),
            json=body,
            headers=headers,
        ).status_code
        == 404
    )
    response = signed.post(path(value), json=body, headers=headers)
    assert response.status_code == 202
    assert (
        signed.post(path(value), json=body, headers=headers).json()["id"] == response.json()["id"]
    )
    assert signed.get(path(value)).json()["state"] == "removing"


def test_worker_removes_only_app_resources_and_retains_registration(signed, setup_runtime, store):
    value = setup_runtime
    run = enqueue(value)
    assert process_one(store, reader(), remover=remover(value))
    assert store.run(run["id"])["status"] == "succeeded"
    result = signed.get(path(value)).json()
    assert result["state"] == "removed"
    assert "persistent_volumes" in result["run"]["result"]["preserved"]
    assert store.service(value["service"]["id"])["id"] == value["service"]["id"]
    assert not value["items"]
    assert enqueue(value, "repeated-after-success")["id"] == run["id"]


@pytest.mark.parametrize("change", ["uid", "owner"])
def test_foreign_or_replaced_resource_prevents_any_deletion(setup_runtime, store, change):
    value = setup_runtime
    run = enqueue(value)
    meta = value["items"]["deployments"]["metadata"]
    if change == "uid":
        meta["uid"] = str(uuid4())
    else:
        meta["labels"]["delta-platform/service-id"] = str(uuid4())
    process_one(store, reader(), remover=remover(value))
    assert store.run(run["id"])["error"]["code"] == "runtime_owner_changed"
    assert all(method != "DELETE" for method, _ in value["calls"])


def test_partial_failure_preserves_record_and_can_resume(setup_runtime, store):
    value = setup_runtime
    run = enqueue(value)
    process_one(store, reader(), remover=remover(value, fail_after_one=True))
    failed = store.run(run["id"])
    assert failed["status"] == "failed" and "token" not in json.dumps(failed, default=str)
    assert "services" not in value["items"] and "deployments" in value["items"]
    with pytest.raises(Problem) as rejected:
        value["envs"].bind(value["service"]["id"], value["body"])
    assert rejected.value.code == "runtime_removal_pending"
    with pytest.raises(Problem):
        store.enqueue(value["service"]["id"], "source-while-partial")
    again = enqueue(value, "retry-after-failure")
    process_one(store, reader(), remover=remover(value))
    assert store.run(again["id"])["status"] == "succeeded"


def test_pending_foreground_deletion_is_not_success(setup_runtime, store):
    value = setup_runtime
    run = enqueue(value)
    process_one(store, reader(), remover=remover(value, pending=True))
    assert store.run(run["id"])["error"]["code"] == "runtime_cleanup_pending"
    assert (
        value["envs"].get(value["service"]["id"], value["environment"]["id"])["binding"][
            "runtime_status"
        ]
        == "removing"
    )


def test_expired_worker_cannot_delete(setup_runtime, store, engine):
    value = setup_runtime
    run = enqueue(value)
    old = store.claim()
    expire(engine, run["id"])
    with pytest.raises(LeaseLost):
        remover(value).execute(old, store, lambda *_: None)
    assert not value["calls"]


def test_storage_backed_app_requires_review_before_any_deletion(setup_runtime, store):
    value = setup_runtime
    value["items"]["deployments"]["spec"]["template"]["spec"]["volumes"] = [
        {"name": "data", "persistentVolumeClaim": {"claimName": "keep-data"}}
    ]
    run = enqueue(value)
    process_one(store, reader(), remover=remover(value))
    assert store.run(run["id"])["error"]["code"] == "runtime_storage_review_required"
    assert all(method != "DELETE" for method, _ in value["calls"])


def test_active_work_and_approved_plan_block_removal(setup_runtime, store, engine):
    from delta_platform.core.schema import workflows

    value = setup_runtime
    active = store.enqueue(value["service"]["id"], "busy-work")[0]
    with pytest.raises(Problem) as exc:
        enqueue(value)
    assert exc.value.code == "service_busy"
    claim = store.claim()
    store.finish(active["id"], claim["lease_token"], error={"code": "test", "message": "done"})
    with engine.begin() as conn:
        conn.execute(
            workflows.insert().values(
                id=uuid4(),
                service_id=value["service"]["id"],
                project_id=value["service"]["project_id"],
                kind="deployment",
                status="approved",
                input_snapshot={},
                input_hash="a" * 64,
                request_key="plan",
            )
        )
    with pytest.raises(Problem) as exc:
        enqueue(value)
    assert exc.value.code == "workflow_busy"


def test_tampered_target_is_rejected_without_network(setup_runtime, store):
    value = setup_runtime
    enqueue(value)
    claim = store.claim()
    claim = copy.deepcopy(claim)
    claim["input_snapshot"]["target"]["cluster_id"] = str(uuid4())
    with pytest.raises(SandboxError, match="runtime_target_changed"):
        remover(value).execute(claim, store, lambda *_: None)
    assert not value["calls"]
