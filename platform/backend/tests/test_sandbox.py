import copy
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from support import LOGIN_ID, PASSWORD
from test_builds import DIGEST, REPOSITORY, Provider
from test_builds import config as build_config
from test_builds import queued as build_queued
from test_builds import runner as build_runner
from test_worker import expire, reader

from delta_platform.adapters.builds import build_target
from delta_platform.adapters.sandbox import SandboxRunner, sandbox_target
from delta_platform.api.app import create_app
from delta_platform.core.errors import Problem
from delta_platform.worker.main import process_one

TARGET = dict(
    cloud="aws",
    region="ap-northeast-2",
    account_id="111122223333",
    cluster="delta-platform-sandbox",
    service="delta-platform-sandbox",
    task_family="delta-platform-sandbox",
    container_name="app",
    target_group_arn="arn:aws:elasticloadbalancing:ap-northeast-2:111122223333:targetgroup/platform/123",
    url="http://sandbox.test",
    allowed_image_prefixes=[REPOSITORY + "@"],
    dynamic_scanner_access=False,
)


def config():
    return build_config().model_copy(update={"sandbox_target": json.dumps(TARGET)})


def queued(store, data):
    build, _ = build_queued(store, data)
    process_one(store, reader(), build_runner(Provider()))
    check, _ = store.enqueue_sandbox(
        build["service_id"], build["id"], "sandbox", sandbox_target(config())
    )
    return check, build


class Ecs:
    def __init__(self):
        self.definitions = {
            "template": dict(
                family=TARGET["task_family"],
                runtimePlatform={"cpuArchitecture": "X86_64", "operatingSystemFamily": "LINUX"},
                containerDefinitions=[
                    dict(
                        name="app",
                        image="bootstrap",
                        environment=[],
                        portMappings=[{"containerPort": 3000}],
                    )
                ],
            )
        }
        self.revision = "template"
        self.count = 0
        self.updates = []
        self.health = []
        self.fail_health = False
        self.wrong_digest = False
        self.cleanup_error = False
        self.interrupt_cleanup = False
        self.interrupt_start = False
        self.on_tasks = None
        self.account = TARGET["account_id"]

    def get_caller_identity(self):
        return {"Account": self.account}

    def describe_services(self, **kwargs):
        return {
            "services": [
                {
                    "status": "ACTIVE",
                    "desiredCount": self.count,
                    "runningCount": self.count,
                    "pendingCount": 0,
                    "taskDefinition": self.revision,
                    "deployments": [{"taskDefinition": self.revision, "rolloutState": "COMPLETED"}],
                }
            ]
        }

    def describe_task_definition(self, taskDefinition):
        item = (
            self.definitions[next(reversed(self.definitions))]
            if taskDefinition == TARGET["task_family"]
            else self.definitions[taskDefinition]
        )
        return {"taskDefinition": copy.deepcopy(item)}

    def register_task_definition(self, **kwargs):
        arn = f"arn:aws:ecs:ap-northeast-2:111122223333:task-definition/{TARGET['task_family']}:{len(self.definitions)}"
        self.definitions[arn] = {**copy.deepcopy(kwargs), "taskDefinitionArn": arn}
        return {"taskDefinition": {"taskDefinitionArn": arn}}

    def update_service(self, **kwargs):
        self.updates.append(kwargs)
        if kwargs["desiredCount"] == 0 and self.cleanup_error:
            raise ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "secret provider message"}},
                "UpdateService",
            )
        self.count = kwargs["desiredCount"]
        self.revision = kwargs.get("taskDefinition", self.revision)
        if self.count == 1 and self.interrupt_start:
            self.interrupt_start = False
            raise ConnectionError("lost activation reply")
        if self.count == 0 and self.interrupt_cleanup:
            self.interrupt_cleanup = False
            raise ConnectionError("lost cleanup reply")
        return {"service": {}}

    def modify_target_group(self, **kwargs):
        self.health.append(kwargs)
        return {}

    def list_tasks(self, **kwargs):
        if self.on_tasks:
            self.on_tasks()
        return {"taskArns": ["task-1"] if self.count else []}

    def describe_tasks(self, **kwargs):
        return {
            "tasks": [
                {
                    "taskArn": "task-1",
                    "taskDefinitionArn": self.revision,
                    "lastStatus": "RUNNING",
                    "containers": [
                        {
                            "name": "app",
                            "imageDigest": "sha256:" + "c" * 64 if self.wrong_digest else DIGEST,
                            "networkInterfaces": [{"privateIpv4Address": "10.0.1.9"}],
                        }
                    ],
                }
            ]
        }

    def describe_target_health(self, **kwargs):
        return {
            "TargetHealthDescriptions": [
                {
                    "Target": {"Id": "10.0.1.9"},
                    "TargetHealth": {"State": "unhealthy" if self.fail_health else "healthy"},
                }
            ]
        }


def runner(p):
    return SandboxRunner(
        config(),
        clients={name: p for name in ("ecs", "sts", "elbv2", "ec2")},
        poll_seconds=0,
        timeout=0,
    )


def test_runtime_result_is_verified_and_cleaned_not_security_approval(store, data):
    run, build = queued(store, data)
    p = Ecs()
    process_one(store, reader(), sandbox=runner(p))
    value = store.run(run["id"])
    assert value["status"] == "succeeded"
    assert value["result"]["image_digest"] == DIGEST
    assert value["result"]["build_id"] == str(build["id"])
    assert (
        value["result"]["runtime_check"] == "passed"
        and value["result"]["security_check"] == "not_run"
    )
    assert value["result"]["cleanup"] == "stopped" and p.count == 0
    assert p.health[0]["HealthCheckPath"] == "/health"
    assert p.updates[0]["loadBalancers"][0]["containerPort"] == 8088
    assert (
        p.definitions[value["external_id"]]["containerDefinitions"][0]["portMappings"][0][
            "containerPort"
        ]
        == 8088
    )
    assert store.service(run["service_id"])["latest_sandbox"]["id"] == run["id"]


def test_repeated_requests_and_same_service_admission(store, data):
    run, build = queued(store, data)
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(
            pool.map(
                lambda _: store.enqueue_sandbox(
                    run["service_id"], build["id"], "sandbox", sandbox_target(config())
                ),
                range(4),
            )
        )
    assert all(not created and value["id"] == run["id"] for value, created in values)
    with pytest.raises(Problem) as exc:
        store.enqueue_sandbox(run["service_id"], uuid4(), "sandbox", sandbox_target(config()))
    assert exc.value.code == "idempotency_conflict"
    with pytest.raises(Problem) as exc:
        store.enqueue_sandbox(run["service_id"], uuid4(), "new", sandbox_target(config()))
    assert exc.value.code == "successful_build_required"
    with pytest.raises(Problem) as exc:
        store.enqueue_sandbox(run["service_id"], build["id"], "new", sandbox_target(config()))
    assert exc.value.code == "sandbox_busy"


def test_other_service_cannot_borrow_build_or_share_active_pool(store, data):
    first, _ = build_queued(store, data)
    process_one(store, reader(), build_runner(Provider()))
    service, _ = store.register(data.model_copy(update={"name": "other"}), "other")
    source, _ = store.enqueue(service["id"], "source")
    process_one(store, reader())
    second, _ = store.enqueue_build(service["id"], source["id"], "build", build_target(config()))
    process_one(store, reader(), build_runner(Provider()))
    with pytest.raises(Problem) as exc:
        store.enqueue_sandbox(service["id"], first["id"], "wrong-build", sandbox_target(config()))
    assert exc.value.code == "successful_build_required"
    store.enqueue_sandbox(first["service_id"], first["id"], "first", sandbox_target(config()))
    with pytest.raises(Problem) as exc:
        store.enqueue_sandbox(service["id"], second["id"], "second", sandbox_target(config()))
    assert exc.value.code == "sandbox_busy"


def test_active_worker_lock_prevents_replacement_side_effects(store, data, engine):
    run, _ = queued(store, data)
    p = Ecs()
    with store.sandbox_lock() as acquired:
        assert acquired
        process_one(store, reader(), sandbox=runner(p))
        assert not p.updates and len(p.definitions) == 1
    expire(engine, run["id"])
    process_one(store, reader(), sandbox=runner(p))
    assert store.run(run["id"])["status"] == "succeeded"


def test_owner_changed_during_check_is_never_stopped(store, data):
    run, _ = queued(store, data)
    p = Ecs()
    p.on_tasks = lambda: setattr(p, "revision", "template")
    process_one(store, reader(), sandbox=runner(p))
    assert p.count == 1 and all(u["desiredCount"] != 0 for u in p.updates)
    assert store.run(run["id"])["error"]["code"] == "sandbox_cleanup_required"


def test_foreign_ecs_execution_is_not_replaced_or_stopped(store, data):
    run, _ = queued(store, data)
    p = Ecs()
    p.count = 1
    process_one(store, reader(), sandbox=runner(p))
    assert store.run(run["id"])["error"]["code"] == "sandbox_occupied"
    assert p.count == 1 and not p.updates and len(p.definitions) == 1


@pytest.mark.parametrize("failure", ["digest", "health", "account"])
def test_unverified_execution_never_reports_success(store, data, failure):
    run, _ = queued(store, data)
    p = Ecs()
    p.wrong_digest = failure == "digest"
    p.fail_health = failure == "health"
    if failure == "account":
        p.account = "999999999999"
    process_one(store, reader(), sandbox=runner(p))
    value = store.run(run["id"])
    assert value["status"] == "failed" and value["result"] is None and p.count == 0
    if failure == "account":
        assert not p.updates


def test_lost_activation_reply_resumes_exact_revision(store, data, engine):
    run, _ = queued(store, data)
    p = Ecs()
    p.interrupt_start = True
    process_one(store, reader(), sandbox=runner(p))
    assert store.run(run["id"])["status"] == "running"
    expire(engine, run["id"])
    process_one(store, reader(), sandbox=runner(p))
    assert store.run(run["id"])["status"] == "succeeded"
    assert len(p.definitions) == 2 and sum(u["desiredCount"] == 1 for u in p.updates) == 1


def test_lost_cleanup_reply_does_not_redeploy(store, data, engine):
    run, _ = queued(store, data)
    p = Ecs()
    p.interrupt_cleanup = True
    process_one(store, reader(), sandbox=runner(p))
    expire(engine, run["id"])
    process_one(store, reader(), sandbox=runner(p))
    assert store.run(run["id"])["status"] == "succeeded"
    assert sum(u["desiredCount"] == 1 for u in p.updates) == 1 and p.count == 0


def test_cleanup_failure_is_visible_without_false_pass(store, data):
    run, _ = queued(store, data)
    p = Ecs()
    p.cleanup_error = True
    process_one(store, reader(), sandbox=runner(p))
    value = store.run(run["id"])
    assert value["status"] == "failed" and value["error"]["code"] == "sandbox_cleanup_required"
    assert value["external_id"] and value["result"] is None and "secret provider" not in str(value)


def test_queued_cancel_never_starts_ecs(store, data):
    run, _ = queued(store, data)
    p = Ecs()
    store.cancel_sandbox(run["id"])
    assert not process_one(store, reader(), sandbox=runner(p))
    assert store.run(run["id"])["error"]["code"] == "sandbox_cancelled" and not p.updates


def test_running_cancel_stops_own_execution(store, data):
    run, _ = queued(store, data)
    p = Ecs()
    p.on_tasks = lambda: store.cancel_sandbox(run["id"])
    process_one(store, reader(), sandbox=runner(p))
    assert store.run(run["id"])["error"]["code"] == "sandbox_cancelled" and p.count == 0
    assert [u["desiredCount"] for u in p.updates] == [1, 0]


def test_expired_worker_cannot_stop_or_publish_after_lease_loss(store, data, engine):
    run, _ = queued(store, data)
    p = Ecs()
    p.on_tasks = lambda: expire(engine, run["id"])
    process_one(store, reader(), sandbox=runner(p))
    assert store.run(run["id"])["status"] == "running" and p.count == 1
    assert [u["desiredCount"] for u in p.updates] == [1]
    p.on_tasks = None
    process_one(store, reader(), sandbox=runner(p))
    assert store.run(run["id"])["status"] == "succeeded" and p.count == 0


def test_api_auth_csrf_idempotency_and_cancel(engine, store, data):
    run, build = queued(store, data)
    with TestClient(create_app(config(), engine), base_url="http://127.0.0.1:8088") as c:
        path = f"/api/v1/sandbox-checks/{run['id']}"
        assert c.get(path).status_code == 401
        c.headers.update({"Origin": "http://127.0.0.1:8088", "X-Platform-Request": "1"})
        login = c.post(
            "/api/v1/auth/login", json={"login_id": LOGIN_ID, "password": PASSWORD}
        ).json()
        assert c.post(path + "/cancel", json={}).status_code == 403
        c.headers.update({"X-CSRF-Token": login["csrf_token"], "Idempotency-Key": "sandbox"})
        r = c.post(
            f"/api/v1/services/{run['service_id']}/sandbox-checks",
            json={"build_id": str(build["id"])},
        )
        assert r.status_code == 200 and r.json()["id"] == str(run["id"])
        assert c.get("/api/v1/builds/" + str(run["id"])).status_code == 404
        assert c.post(path + "/cancel", json={}).status_code == 200
        assert c.get(path + "/events").json()["items"][-1]["event_type"] == "cancel_requested"


def test_unconfigured_target_does_not_hide_as_success():
    with pytest.raises(Problem) as exc:
        sandbox_target(build_config())
    assert exc.value.code == "sandbox_not_configured"
    with pytest.raises(Problem):
        sandbox_target(
            config().model_copy(
                update={"sandbox_target": json.dumps({**TARGET, "service": "delta-lab-staging"})}
            )
        )
