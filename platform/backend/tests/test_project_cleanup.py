import copy
import importlib.util
import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from botocore.exceptions import ClientError
from support import settings
from test_builds import REPOSITORY, Provider, queued, runner
from test_deployments import configured, setup
from test_runtime_removal import remover
from test_runtime_removal import setup_runtime as setup_runtime
from test_worker import reader

from delta_platform.adapters.project_removal import ProjectRemover
from delta_platform.core.errors import Problem
from delta_platform.core.project_removal import ProjectRemovalStore
from delta_platform.core.schema import environments, runs
from delta_platform.worker.main import process_one


def test_project_cleanup_waits_for_resources_and_is_retry_safe(setup_runtime, store, signed):
    v = setup_runtime
    sid = v["service"]["id"]
    uri = f"/api/v1/services/{sid}/delete"
    body = {"confirmation_name": v["service"]["name"]}
    first = signed.post(uri, json=body)
    assert first.status_code == 202
    assert signed.post(uri, json=body).json() == first.json()
    assert store.service(sid)["deletion_requested"]
    with pytest.raises(Problem, match="프로젝트 자원 삭제"):
        store.enqueue(sid, "new-work")
    process_one(
        store,
        reader(),
        project_remover=ProjectRemover(v["config"], remover=remover(v, fail_after_one=True)),
    )
    state = signed.get(f"/api/v1/services/{sid}/deletion").json()
    assert state["status"] == "failed"
    assert store.service(sid)
    assert "services" not in v["items"] and "deployments" in v["items"]
    retry = signed.post(uri, json=body)
    assert retry.status_code == 202
    process_one(store, reader(), project_remover=ProjectRemover(v["config"], remover=remover(v)))
    assert not v["items"]
    assert signed.get(f"/api/v1/services/{sid}/deletion").json()["status"] == "deleted"
    assert signed.get(f"/api/v1/services/{sid}").status_code == 404
    assert signed.post(uri, json=body).status_code == 204
    with store.engine.connect() as conn:
        assert not conn.scalar(sa.select(environments.c.id).where(environments.c.service_id == sid))
        assert not conn.scalar(sa.select(runs.c.id).where(runs.c.service_id == sid))


def test_project_owner_changed_never_deletes_any_resource(setup_runtime, store):
    v = setup_runtime
    store.delete_service(v["service"]["id"], v["service"]["name"], v["config"])
    v["items"]["deployments"]["metadata"]["uid"] = str(uuid4())
    process_one(store, reader(), project_remover=ProjectRemover(v["config"], remover=remover(v)))
    assert all(method != "DELETE" for method, _ in v["calls"])
    assert store.service(v["service"]["id"])["deletion_requested"]


def test_failed_deployments_without_environment_are_included(engine, store, data):
    plans, sid, body = setup(engine, store, data, source=False)
    plan, _ = plans.create(sid, body, "plan")
    plans.execute(plan["id"], plan["input_hash"], "operator")
    with engine.begin() as conn:
        conn.execute(
            runs.update()
            .where(runs.c.service_id == sid, runs.c.kind == "service_deploy")
            .values(
                status="failed",
                finished_at=sa.func.now(),
                error={"code": "lost", "message": "VM created but health failed"},
                submission={"queue_id": 1},
            )
        )
    result = store.delete_service(sid, store.service(sid)["name"], configured())
    with engine.connect() as conn:
        snapshot = conn.scalar(
            sa.select(runs.c.input_snapshot).where(runs.c.id == result["run"]["id"])
        )
    assert len(snapshot["deployments"]) == 1
    assert snapshot["deployments"][0]["target"]["cloud"] == "gcp"
    assert snapshot["deployments"][0]["environment_id"]


class Registry:
    def __init__(self):
        self.present = True
        self.deleted = []

    def describe_images(self, **args):
        if not self.present:
            raise ClientError({"Error": {"Code": "ImageNotFoundException"}}, "DescribeImages")
        return {"imageDetails": [{"imageDigest": "sha256:" + "b" * 64}]}

    def batch_delete_image(self, **args):
        self.deleted.append(args)
        self.present = False
        return {"failures": []}


def test_build_tags_removed_but_shared_repository_kept(store, data):
    build, _ = queued(store, data)
    process_one(store, reader(), runner(Provider()))
    cfg = settings().model_copy(update={"build_repository": REPOSITORY})
    service = store.service(build["service_id"])
    store.delete_service(service["id"], service["name"], cfg)
    registry = Registry()
    process_one(store, reader(), project_remover=ProjectRemover(cfg, ecr=registry))
    assert registry.deleted[0]["imageIds"] == [{"imageTag": "run-" + str(build["id"])}]
    assert ProjectRemovalStore(store.engine).status(service["id"])["status"] == "deleted"


def test_shared_digest_is_preserved(store, data, engine):
    a, _ = queued(store, data)
    process_one(store, reader(), runner(Provider()))
    other = store.register(data.model_copy(update={"name": "other"}), "other")[0]
    work = store.enqueue(other["id"], "ref")[0]
    with engine.begin() as conn:
        conn.execute(
            runs.update()
            .where(runs.c.id == work["id"])
            .values(input_snapshot={"image": REPOSITORY + "@sha256:" + "b" * 64})
        )
    cfg = settings().model_copy(update={"build_repository": REPOSITORY})
    registry = Registry()
    ProjectRemover(cfg, ecr=registry).remove_image(
        {"repository": REPOSITORY, "region": cfg.build_region, "tag": "run-" + str(a["id"])},
        {"service_id": a["service_id"]},
        store,
    )
    assert not registry.deleted


def cleanup_module():
    directory = Path(__file__).resolve().parents[2] / "deploy/workloads"
    sys.path.insert(0, str(directory))
    spec = importlib.util.spec_from_file_location("workload_cleanup", directory / "remove.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.path.remove(str(directory))
    return module


def test_destroy_plan_refuses_other_customers_or_extra_resources():
    module = cleanup_module()
    data = {"service_id": str(uuid4()), "environment_id": str(uuid4()), "target": {"cloud": "aws"}}
    change = {
        "address": "aws_instance.app",
        "change": {
            "actions": ["delete"],
            "before": {
                "tags": {
                    "ManagedBy": "delta-platform",
                    "ServiceId": data["service_id"],
                    "Environment": data["environment_id"],
                }
            },
        },
    }
    module.validate_changes({"resource_changes": [change]}, data)
    for kind in ("other_owner", "other_resource", "additional_disk"):
        bad = copy.deepcopy(change)
        if kind == "other_owner":
            bad["change"]["before"]["tags"]["ServiceId"] = str(uuid4())
        if kind == "other_resource":
            bad["address"] = "aws_vpc.shared"
        if kind == "additional_disk":
            bad["change"]["before"]["ebs_block_device"] = [{"volume_id": "external"}]
        with pytest.raises(ValueError):
            module.validate_changes({"resource_changes": [bad]}, data)


def test_wrong_cleanup_ack_cannot_remove_records(setup_runtime, store):
    v = setup_runtime
    store.delete_service(v["service"]["id"], v["service"]["name"], v["config"])
    claim = store.claim()
    with pytest.raises(ValueError):
        store.finish(
            claim["id"],
            claim["lease_token"],
            result={"cleanup": "complete", "snapshot_hash": "wrong"},
        )
    assert store.service(v["service"]["id"])


@pytest.mark.parametrize("corrupt", [False, True])
def test_k3s_cleanup_checks_all_owners_and_uses_delete_preconditions(monkeypatch, corrupt):
    module = cleanup_module()
    selected = {
        "namespace": "delta-apps",
        "namespace_uid": "ns-id",
        "cluster_id": "cluster-id",
        "kubeconfig": "/trusted/config",
        "private_ip": "10.40.0.10",
    }
    data = {"plan_hash": "a" * 64}
    labels = {"delta-platform/service-id": "service-owner"}
    objects = [
        {"kind": k, "metadata": {"name": "app-test", "labels": labels}}
        for k in ("NetworkPolicy", "Deployment", "Service")
    ]
    calls = []
    monkeypatch.setattr(module, "k3s_objects", lambda *_: objects)

    def command(args, **kwargs):
        calls.append((args, kwargs))
        if "namespace" in args:
            name = args[args.index("namespace") + 1]
            return json.dumps(
                {"metadata": {"uid": "cluster-id" if name == "kube-system" else "ns-id"}}
            )
        if "get" in args:
            kind = args[args.index("get") + 1]
            return json.dumps(
                {
                    "metadata": {
                        "name": "app-test",
                        "uid": "owned-uid",
                        "resourceVersion": "7",
                        "labels": {} if corrupt and kind == "Service" else labels,
                        "annotations": {"delta-platform/plan-hash": data["plan_hash"]},
                    },
                    "spec": {"template": {"spec": {"containers": [{"name": "app"}]}}},
                }
            )
        return ""

    monkeypatch.setattr(module, "run", command)
    if corrupt:
        with pytest.raises(ValueError):
            module.onprem(data, selected)
        assert not any("delete" in args for args, _ in calls)
    else:
        module.onprem(data, selected)
        deletes = [json.loads(kwargs["body"]) for args, kwargs in calls if "delete" in args]
        assert len(deletes) == 3
        assert all(
            d["preconditions"] == {"uid": "owned-uid", "resourceVersion": "7"} for d in deletes
        )
        assert all("default-deny" not in args for args, _ in calls)
