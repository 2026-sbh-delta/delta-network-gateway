import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr
from support import settings
from test_workflows import setup_plan

from delta_platform.adapters.onprem_removal import OnpremRemover
from delta_platform.adapters.workflow_diff import WorkflowDiff, projection
from delta_platform.adapters.workload_inventory import COVERAGE, collect
from delta_platform.core.errors import Problem
from delta_platform.core.schema import environments


def result(context):
    return {
        "context": context,
        "source": {"serial": 21},
        "assessment": {
            "assessment_id": context["run_id"],
            "kind": "migration_mapping",
            "verdict": "needs_human",
            "mapping": {
                "rows": [
                    {
                        "resource": "security",
                        "source": "RDS_LOGIN_EVENTS",
                        "target": "",
                        "grade": "gap",
                        "differences": ["확인 필요"],
                    }
                ]
            },
        },
    }


def test_existing_workflow_gets_bound_public_projection_and_cached_result(
    engine, store, data, signed
):
    workflows, service_id, _, target, body = setup_plan(engine, store, data)
    wf, _ = workflows.create(service_id, body, "workflow")
    calls = []
    service = WorkflowDiff(settings(), workflows)

    def proxy(body):
        calls.append(body)
        return result(body["context"])

    service._request = proxy
    first = service.execute(wf["id"], target, "diff")
    again = service.execute(wf["id"], target, "diff")
    assert first == again and len(calls) == 1
    public = service.public_results(wf["id"], workflows.executions(wf["id"]))
    assert (
        public["items"][0]["assessment"]["assessment_id"]
        == str(public["items"][0]["id"])
        == first["assessment_id"]
    )
    assert first["plan_hash"] == wf["input_hash"] and first["changes"] == []
    public_http = signed.get(f"/api/v1/workflows/{wf['id']}/executions")
    assert public_http.status_code == 200 and public_http.json()["items"][0]["assessment"] == first
    assert workflows.get(wf["id"])["status"] == "planned"
    # The result cannot be projected after the operator changes the target binding.
    with engine.begin() as conn:
        conn.execute(
            environments.update()
            .where(environments.c.id == target)
            .values(binding={"changed": True})
        )
    with pytest.raises(Problem):
        service.public_results(wf["id"], workflows.executions(wf["id"]))


def test_wrong_context_fails_closed_and_records_failure(engine, store, data):
    workflows, service_id, _, target, body = setup_plan(engine, store, data)
    wf, _ = workflows.create(service_id, body, "workflow")
    service = WorkflowDiff(settings(), workflows)

    def proxy(body):
        response = result(body["context"])
        response["context"] = {**body["context"], "run_id": str(uuid4())}
        return response

    service._request = proxy
    with pytest.raises(Problem, match="차분"):
        service.execute(wf["id"], target, "diff")
    assert any(i["status"] == "failed" for i in workflows.executions(wf["id"])["items"])
    assert workflows.get(wf["id"])["status"] == "planned"


def test_unknown_target_and_public_untrusted_manifest_rejected(engine, store, data, signed):
    workflows, service_id, _, target, body = setup_plan(engine, store, data)
    wf, _ = workflows.create(service_id, body, "workflow")
    path = f"/api/v1/workflows/{wf['id']}/diff"
    assert (
        signed.post(
            path,
            json={"environment_id": str(target), "source_manifests": "untrusted"},
            headers={"Idempotency-Key": "diff"},
        ).status_code
        == 422
    )
    assert (
        signed.post(
            path, json={"environment_id": str(uuid4())}, headers={"Idempotency-Key": "diff"}
        ).status_code
        == 409
    )


def test_https_login_and_diff_relay_do_not_follow_redirects_or_expose_credentials():
    requests = []
    config = SimpleNamespace(
        orchestrator_url="https://orchestrator.example",
        orchestrator_password=SecretStr("credential-for-tests-only"),
    )

    def handler(request):
        requests.append(request)
        if request.url.path == "/login":
            return httpx.Response(
                303, headers={"set-cookie": "__Host-delta_session=test; Path=/; Secure"}
            )
        assert request.headers["cookie"] == "__Host-delta_session=test"
        return httpx.Response(200, json={"ok": True})

    service = WorkflowDiff(
        config, SimpleNamespace(engine=None), transport=httpx.MockTransport(handler)
    )
    assert service._request({"context": {}}) == {"ok": True}
    assert [r.url.path for r in requests] == ["/login", "/api/platform-diff"]
    assert all(r.headers["origin"] == config.orchestrator_url for r in requests)
    bad = SimpleNamespace(
        orchestrator_url="http://evil.example", orchestrator_password=config.orchestrator_password
    )
    with pytest.raises(Problem):
        WorkflowDiff(bad, SimpleNamespace(engine=None))._request({})


def test_missing_observations_never_become_security_pass():
    context = {
        "run_id": "check",
        "workflow_id": "plan",
        "environment_id": "target",
        "plan_hash": "hash",
    }
    reply = result(context)
    reply["assessment"]["verdict"] = "pass"
    reply["assessment"]["mapping"]["rows"][0]["grade"] = "exact"
    view = projection(reply, context, [COVERAGE])
    assert "needs_human" in view["summary"] and view["differences"][-1]["judgment"] == "needs_check"


def test_plan_creation_runs_diff_and_existing_screen_receives_its_table(
    engine, store, data, signed, monkeypatch
):
    workflows, service_id, _, _, body = setup_plan(engine, store, data)
    monkeypatch.setenv("PLATFORM_ORCHESTRATOR_URL", "https://configured.example")
    monkeypatch.setattr(WorkflowDiff, "_request", lambda self, body: result(body["context"]))
    created = signed.post(
        f"/api/v1/services/{service_id}/workflows",
        json=body.model_dump(mode="json"),
        headers={"Idempotency-Key": "auto-diff"},
    )
    assert created.status_code == 201
    wf = created.json()
    entries = signed.get(f"/api/v1/workflows/{wf['id']}/executions").json()["items"]
    assert entries[0]["assessment"]["differences"][0]["source"] == "RDS_LOGIN_EVENTS"
    assert entries[0]["assessment"]["plan_hash"] == wf["input_hash"]
    assert workflows.get(UUID(wf["id"]))["status"] == "planned"


def test_inventory_only_reads_registered_uid_and_strips_secret_env_and_commands():
    cluster, uid, service_id, env_id = (str(uuid4()) for _ in range(4))
    resource = {"kind": "Deployment", "name": "app", "uid": uid}
    env = {
        "id": env_id,
        "service_id": service_id,
        "binding": {
            "runtime": {"cluster_id": cluster, "namespace": "delta-apps", "resources": [resource]}
        },
    }
    calls = []
    item = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "app",
            "namespace": "delta-apps",
            "uid": uid,
            "labels": {
                "app.kubernetes.io/managed-by": "delta-platform",
                "delta-platform/service-id": service_id,
                "delta-platform/environment-id": env_id,
            },
        },
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "app",
                            "image": "registry/app@sha256:" + "a" * 64,
                            "env": [{"name": "PASSWORD", "value": "SECRET"}],
                            "command": ["SECRET"],
                            "args": ["SECRET"],
                        }
                    ]
                }
            }
        },
    }

    class Reader:
        target = {"cluster_id": cluster, "namespaces": ["delta-apps"]}
        owned = OnpremRemover.owned

        def __init__(self, _):
            pass

        def call(self, method, namespace, resource):
            calls.append(method)
            return item

    manifest = collect(None, env, reader_factory=Reader)
    assert calls == ["GET"] and "SECRET" not in manifest and '"env"' not in manifest
    assert json.loads(manifest)["items"][0]["metadata"]["uid"] == uid
    item["metadata"]["uid"] = str(uuid4())
    with pytest.raises(Problem):
        collect(None, env, reader_factory=Reader)


@pytest.mark.parametrize("mismatch", [None, "environment_id", "binding_hash", "uid", "expired"])
def test_snapshot_requires_a_fresh_exact_registered_inventory(tmp_path, mismatch):
    from datetime import UTC, datetime, timedelta

    from delta_platform.adapters.workload_inventory import read_snapshot
    from delta_platform.core.store import digest

    service, env_id, cluster, uid = (str(uuid4()) for _ in range(4))
    resource = {"kind": "Deployment", "name": "app", "uid": uid}
    environment = {
        "id": env_id,
        "service_id": service,
        "binding": {
            "runtime": {"cluster_id": cluster, "namespace": "delta-apps", "resources": [resource]}
        },
    }
    now = datetime(2026, 11, 7, 5, 0, tzinfo=UTC)
    observed = now - timedelta(hours=25 if mismatch == "expired" else 1)
    snapshot = {
        "environment_id": str(uuid4()) if mismatch == "environment_id" else env_id,
        "service_id": service,
        "binding_hash": "a" * 64 if mismatch == "binding_hash" else digest(environment["binding"]),
        "captured_at": observed.isoformat(),
        "items": [
            {
                "kind": "Deployment",
                "metadata": {
                    "namespace": "delta-apps",
                    "name": "app",
                    "uid": str(uuid4()) if mismatch == "uid" else uid,
                },
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": "app",
                                    "image": "app:1",
                                    "env": [{"name": "PASSWORD", "value": "SECRET"}],
                                }
                            ]
                        }
                    }
                },
            }
        ],
    }
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(snapshot))
    if mismatch:
        with pytest.raises(ValueError):
            read_snapshot(environment, path=path, now=now)
    else:
        manifest, note = read_snapshot(environment, path=path, now=now)
        assert uid in manifest and "SECRET" not in manifest
        assert observed.isoformat() in note
