from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from support import LOGIN_ID
from test_builds import Provider, queued, runner
from test_worker import reader

from delta_platform.core.environments import EnvironmentInput, EnvironmentStore
from delta_platform.core.errors import Problem
from delta_platform.core.schema import environments, workflow_executions
from delta_platform.core.store import digest
from delta_platform.core.workflows import WorkflowInput, WorkflowStore
from delta_platform.worker.main import process_one


def setup_plan(engine, store, data):
    build, _ = queued(store, data)
    process_one(store, reader(), runner(Provider()))
    envs = EnvironmentStore(engine)
    source = envs.bind(
        build["service_id"],
        EnvironmentInput(
            name="AWS", cloud="aws", region="ap-northeast-2", account_id="123456789012"
        ),
    )
    target = envs.bind(
        build["service_id"],
        EnvironmentInput(
            name="GCP", cloud="gcp", region="asia-northeast3", gcp_project="term2-delta"
        ),
    )
    body = WorkflowInput(
        build_id=build["id"],
        source_environment_id=source,
        target_environment_ids=[target],
        actions=[{"environment_id": target, "kind": "app_deployment", "payload_hash": digest({})}],
    )
    return WorkflowStore(engine), build["service_id"], source, target, body


def test_owned_plan_retry_and_approval_api(engine, store, data, signed):
    workflows, service, _, _, body = setup_plan(engine, store, data)
    path = f"/api/v1/services/{service}/workflows"
    response = signed.post(
        path, json=body.model_dump(mode="json"), headers={"Idempotency-Key": "plan"}
    )
    assert response.status_code == 201
    wf = response.json()
    again = signed.post(
        path, json=body.model_dump(mode="json"), headers={"Idempotency-Key": "plan"}
    )
    assert again.status_code == 200 and again.json()["id"] == wf["id"]
    assert signed.get(path).json()["items"][0]["id"] == wf["id"]
    assert wf["project_id"] == str(store.service(service)["project_id"])
    assert wf["input_snapshot"]["image"].endswith(wf["input_snapshot"]["image_digest"])
    approve = f"/api/v1/workflows/{wf['id']}/approval"
    assert signed.post(approve, json={"input_hash": "0" * 64}).status_code == 409
    assert (
        signed.post(approve, json={"input_hash": wf["input_hash"], "actor": "forged"}).status_code
        == 422
    )
    result = signed.post(approve, json={"input_hash": wf["input_hash"]})
    assert result.status_code == 200 and result.json()["approved_by"] == LOGIN_ID
    assert workflows.get(UUID(wf["id"]))["status"] == "approved"


def test_cross_service_environment_and_build_are_rejected(engine, store, data):
    workflows, service, _, target, body = setup_plan(engine, store, data)
    other, _ = store.register(data.model_copy(update={"name": "other"}), "other")
    with pytest.raises(Problem) as exc:
        workflows.create(other["id"], body, "plan")
    assert exc.value.code == "build_not_ready"
    other_target = EnvironmentStore(engine).bind(
        other["id"], EnvironmentInput(name="local", cloud="onprem", region="local-pc")
    )
    with pytest.raises(Problem) as exc:
        workflows.create(
            service, body.model_copy(update={"target_environment_ids": [other_target]}), "plan"
        )
    assert exc.value.code == "environment_not_owned"
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                environments.update().where(environments.c.id == target).values(project_id=uuid4())
            )


def test_execution_retries_concurrently_and_changed_input_needs_new_id(engine, store, data):
    workflows, service, _, target, body = setup_plan(engine, store, data)
    wf, _ = workflows.create(service, body, "plan")
    with ThreadPoolExecutor(max_workers=4) as pool:
        contexts = list(
            pool.map(
                lambda _: workflows.prepare_execution(
                    wf["id"], target, "ai_assessment", {"observed": True}, "check"
                ),
                range(4),
            )
        )
    assert len({ctx["run_id"] for ctx in contexts}) == 1
    with pytest.raises(Problem) as exc:
        workflows.prepare_execution(wf["id"], target, "ai_assessment", {"observed": False}, "check")
    assert exc.value.code == "idempotency_conflict"
    changed = workflows.prepare_execution(
        wf["id"], target, "ai_assessment", {"observed": False}, "check-new"
    )
    assert changed["run_id"] != contexts[0]["run_id"]
    with pytest.raises(Problem) as exc:
        workflows.prepare_execution(wf["id"], target, "app_deployment", {}, "deploy")
    assert exc.value.code == "approval_required"
    workflows.approve(wf["id"], wf["input_hash"], "operator")
    # Approval does not change the immutable context of an in-flight assessment.
    ctx, _ = workflows.guard(wf["id"], UUID(contexts[0]["run_id"]))
    assert ctx == contexts[0]
    deployed = workflows.prepare_execution(wf["id"], target, "app_deployment", {}, "deploy")
    assert workflows.guard(wf["id"], UUID(deployed["run_id"]), require_approval=True)[0] == deployed
    with pytest.raises(Problem) as exc:
        workflows.prepare_execution(
            wf["id"], target, "app_deployment", {"different": True}, "changed-deploy"
        )
    assert exc.value.code == "action_not_approved"


def test_environment_or_input_change_invalidates_execution(engine, store, data):
    workflows, service, _, target, body = setup_plan(engine, store, data)
    wf, _ = workflows.create(service, body, "plan")
    ctx = workflows.prepare_execution(
        wf["id"], target, "ai_assessment", {"observed": True}, "check"
    )
    run_id = UUID(ctx["run_id"])
    with engine.begin() as conn:
        conn.execute(
            workflow_executions.update()
            .where(workflow_executions.c.id == run_id)
            .values(payload={"observed": False})
        )
    with pytest.raises(Problem) as exc:
        workflows.guard(wf["id"], run_id)
    assert exc.value.code == "execution_input_changed"
    with engine.begin() as conn:
        conn.execute(
            environments.update()
            .where(environments.c.id == target)
            .values(binding={"gcp_project": "different"})
        )
    with pytest.raises(Problem) as exc:
        workflows.approve(wf["id"], wf["input_hash"], "operator")
    assert exc.value.code == "plan_target_changed"


def test_result_cannot_cross_workflows_or_overwrite_completed_result(engine, store, data):
    workflows, service, _, target, body = setup_plan(engine, store, data)
    wf, _ = workflows.create(service, body, "plan")
    other, _ = workflows.create(service, body, "other-plan")
    ctx = workflows.prepare_execution(
        wf["id"], target, "ai_assessment", {"observed": True}, "check"
    )
    run_id = UUID(ctx["run_id"])
    with pytest.raises(Problem) as exc:
        workflows.guard(other["id"], run_id)
    assert exc.value.code == "execution_not_owned"
    with pytest.raises(Problem) as exc:
        workflows.complete_execution(wf["id"], run_id, {**ctx, "environment_id": str(uuid4())}, {})
    assert exc.value.code == "execution_result_mismatch"
    workflows.complete_execution(wf["id"], run_id, ctx, {"verdict": "pass"})
    workflows.complete_execution(wf["id"], run_id, ctx, {"verdict": "pass"})
    with pytest.raises(Problem) as exc:
        workflows.complete_execution(wf["id"], run_id, ctx, {"verdict": "fix"})
    assert exc.value.code == "execution_result_conflict"
    assert "payload" not in workflows.executions(wf["id"])["items"][0]
