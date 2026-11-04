import copy
from uuid import uuid4

import pytest
from test_builds import DIGEST, REPOSITORY, Provider
from test_builds import queued as build_queued
from test_builds import runner as build_runner
from test_sandbox import config as aws_config
from test_worker import expire, reader

from delta_platform.adapters.gcp_sandbox import CloudRun, GcpSandboxRunner, GcpTarget
from delta_platform.adapters.sandbox import SandboxError, sandbox_target
from delta_platform.core.errors import LeaseLost, Problem
from delta_platform.worker.main import process_one

TARGET = GcpTarget(
    project="test-project",
    repository="asia-northeast3-docker.pkg.dev/test-project/sandbox/app",
    runtime_service_account="runtime@test-project.iam.gserviceaccount.com",
    runner_service_account="runner@test-project.iam.gserviceaccount.com",
    scanner_service_account="scanner@test-project.iam.gserviceaccount.com",
    network="projects/test-project/global/networks/sandbox",
    subnet="projects/test-project/regions/asia-northeast3/subnetworks/sandbox",
    allowed_image_prefixes=[REPOSITORY + "@"],
)


def config():
    return aws_config().model_copy(update={"gcp_sandbox_target": TARGET.model_dump_json()})


def queued(store, data):
    build, _ = build_queued(store, data)
    process_one(store, reader(), build_runner(Provider()))
    run, _ = store.enqueue_sandbox(build["service_id"], build["id"], "sandbox", TARGET.model_dump())
    return run, build


class Copy:
    def __init__(self):
        self.calls = []

    def copy(self, image, guard):
        guard()
        self.calls.append(image)
        return TARGET.repository + "@" + DIGEST


class Run(CloudRun):
    def __init__(self):
        self.target = TARGET
        self.current = None
        self.creates = 0
        self.fail_delete = False
        self.lost_create = False
        self.cancel = None

    def get(self, resource):
        return self.current

    def create(self, resource, run_id, commit, image, port, guard):
        self.creates += 1
        self.current = {
            "name": resource,
            "labels": {"delta-run-id": str(run_id), "delta-commit": commit},
            "template": {
                "serviceAccount": TARGET.runtime_service_account,
                "containers": [{"image": image}],
                "scaling": {"maxInstanceCount": 1},
                "vpcAccess": {
                    "egress": "ALL_TRAFFIC",
                    "networkInterfaces": [
                        {
                            "network": TARGET.network,
                            "subnetwork": TARGET.subnet,
                            "tags": [TARGET.network_tag],
                        }
                    ],
                },
            },
        }
        if self.lost_create:
            raise LeaseLost()

    def authorize(self, resource):
        pass

    def verify(self, resource, run_id, commit, image, path, guard):
        if self.cancel:
            self.cancel()
        guard()
        self.owned(self.current, run_id, commit, image)
        return {
            "resource": resource,
            "url": "https://owned.run.app",
            "revision": resource + "/revisions/rev",
            "image_digest": DIGEST,
        }

    def delete(self, resource, current, guard):
        guard()
        if self.fail_delete:
            raise ConnectionError("unavailable")
        self.current = None


def runner(p, copier=None):
    return GcpSandboxRunner(config(), provider=p, copier=copier or Copy())


def test_gcp_identity_and_cleanup_are_recorded(store, data):
    run, _ = queued(store, data)
    p, copier = Run(), Copy()
    process_one(store, reader(), sandbox=runner(p, copier))
    value = store.run(run["id"])
    assert value["status"] == "succeeded"
    assert value["result"]["execution_cloud"] == "gcp"
    assert value["result"]["environment_check"] == "gcp_sandbox_only"
    assert value["result"]["runtime_image"] == TARGET.repository + "@" + DIGEST
    assert value["result"]["cleanup"] == "stopped" and p.current is None
    assert len(copier.calls) == p.creates == 1


def test_target_is_part_of_idempotency(store, data):
    run, build = queued(store, data)
    with pytest.raises(Problem) as exc:
        store.enqueue_sandbox(
            run["service_id"], build["id"], "sandbox", sandbox_target(aws_config())
        )
    assert exc.value.code == "idempotency_conflict"


def test_lost_create_resumes_same_resource_without_recopy(store, data, engine):
    run, _ = queued(store, data)
    p, copier = Run(), Copy()
    p.lost_create = True
    process_one(store, reader(), sandbox=runner(p, copier))
    assert p.current and store.run(run["id"])["status"] == "running"
    expire(engine, run["id"])
    process_one(store, reader(), sandbox=runner(p, copier))
    assert store.run(run["id"])["status"] == "succeeded"
    assert p.creates == len(copier.calls) == 1 and p.current is None


def test_cleanup_failure_holds_slot_and_recovery_does_not_reexecute(store, data, engine):
    run, _ = queued(store, data)
    p = Run()
    p.fail_delete = True
    process_one(store, reader(), sandbox=runner(p))
    assert store.run(run["id"])["status"] == "running"
    p.fail_delete = False
    expire(engine, run["id"])
    process_one(store, reader(), sandbox=runner(p))
    assert store.run(run["id"])["status"] == "succeeded"
    assert p.creates == 1 and p.current is None


def test_cancel_deletes_owned_service(store, data):
    run, _ = queued(store, data)
    p = Run()
    p.cancel = lambda: store.cancel_sandbox(run["id"])
    process_one(store, reader(), sandbox=runner(p))
    assert store.run(run["id"])["error"]["code"] == "sandbox_cancelled"
    assert p.current is None


def test_foreign_service_is_never_changed_or_deleted(store, data):
    run, _ = queued(store, data)
    p = Run()
    p.create(
        TARGET.resource(run["id"]),
        run["id"],
        "f" * 40,
        TARGET.repository + "@" + DIGEST,
        8088,
        lambda: None,
    )
    before = copy.deepcopy(p.current)
    process_one(store, reader(), sandbox=runner(p))
    assert store.run(run["id"])["status"] == "running"
    assert p.current == before


@pytest.mark.parametrize("mutation", ["image", "network", "identity", "public", "scaling"])
def test_ownership_requires_digest_network_identity_and_limits(mutation):
    p, run_id = Run(), uuid4()
    image = TARGET.repository + "@" + DIGEST
    p.create(TARGET.resource(run_id), run_id, "a" * 40, image, 8088, lambda: None)
    if mutation == "image":
        p.current["template"]["containers"][0]["image"] = "untrusted:latest"
    elif mutation == "network":
        p.current["template"]["vpcAccess"]["networkInterfaces"][0]["network"] = "production"
    elif mutation == "identity":
        p.current["template"]["serviceAccount"] = "admin"
    elif mutation == "public":
        p.current["invokerIamDisabled"] = True
    else:
        p.current["template"]["scaling"]["maxInstanceCount"] = 100
    with pytest.raises(SandboxError):
        p.owned(p.current, run_id, "a" * 40, image)


def test_unconfigured_cloud_never_falls_back():
    with pytest.raises(Problem):
        sandbox_target(aws_config(), "gcp")
    with pytest.raises(Problem):
        sandbox_target(config(), "onprem")


def test_gcp_dast_uses_actual_origin_and_preserves_original_build(store, data):
    import httpx
    from test_dast import Dast
    from test_dast import config as dast_config
    from test_security import Sast
    from test_security import queued as security_queued
    from test_security import runner as security_runner

    from delta_platform.adapters.dast import DastInspector, dast_target

    security, build = security_queued(store, data)
    sast = Sast()
    process_one(store, reader(), security=security_runner(sast))
    settings = dast_config().model_copy(update={"gcp_sandbox_target": TARGET.model_dump_json()})
    run, _ = store.enqueue_sandbox(
        build["service_id"],
        build["id"],
        "gcp-dast",
        TARGET.model_dump(),
        security_check_id=security["id"],
        dast_target=dast_target(settings),
    )
    remote, provider = Dast(), Run()
    inspector = DastInspector(
        settings,
        transport=httpx.MockTransport(remote),
        sast_transport=httpx.MockTransport(sast),
        poll_seconds=0,
    )
    process_one(
        store,
        reader(),
        dast=GcpSandboxRunner(settings, provider=provider, copier=Copy(), inspector=inspector),
    )
    result = store.run(run["id"])
    assert result["status"] == "succeeded" and provider.current is None
    assert result["result"]["execution_cloud"] == "gcp"
    binding = remote.inputs[str(run["id"])]["binding"]
    assert binding["target_url"] == "https://owned.run.app"
    assert binding["runtime_resource"] == TARGET.resource(run["id"])
    assert binding["image"] == REPOSITORY + "@" + DIGEST
    assert remote.stops == [str(run["id"])]


def test_http_check_waits_for_iam_but_keeps_exact_revision(monkeypatch):
    from types import SimpleNamespace

    p, run_id = Run(), uuid4()
    image = TARGET.repository + "@" + DIGEST
    resource = TARGET.resource(run_id)
    p.create(resource, run_id, "a" * 40, image, 8088, lambda: None)
    revision = resource + "/revisions/" + resource.rsplit("/", 1)[-1] + "-00001-abc"
    p.current.update(
        latestReadyRevision=revision,
        latestCreatedRevision=revision,
        terminalCondition={"state": "CONDITION_SUCCEEDED"},
        uri="https://owned.run.app",
    )
    p.health_timeout, p.poll_seconds = 1, 0
    p.id_token = lambda url: "invocation-token"
    revisions, requests = [], []

    def get_revision(method, path):
        revisions.append(path)
        return {"containers": [{"image": image}]}

    def get_http(url, **kwargs):
        requests.append((url, kwargs))
        return SimpleNamespace(status_code=403 if len(requests) == 1 else 200, close=lambda: None)

    p.call = get_revision
    monkeypatch.setattr("requests.get", get_http)
    value = CloudRun.verify(p, resource, run_id, "a" * 40, image, "/health", lambda: None)
    assert value["revision"] == revision and revisions == [revision]
    assert len(requests) == 2 and requests[-1][0] == "https://owned.run.app/health"
    assert requests[-1][1]["headers"] == {"X-Serverless-Authorization": "Bearer invocation-token"}
    assert requests[-1][1]["allow_redirects"] is False
