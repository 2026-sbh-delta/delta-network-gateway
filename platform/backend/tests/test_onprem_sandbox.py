import base64
import copy
import ssl
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr
from test_builds import DIGEST, REPOSITORY, Provider
from test_builds import queued as build_queued
from test_builds import runner as build_runner
from test_sandbox import config as aws_config
from test_worker import expire, reader

from delta_platform.adapters.onprem_sandbox import K3s, OnpremSandboxRunner, OnpremTarget
from delta_platform.adapters.sandbox import SandboxError, sandbox_target
from delta_platform.core.errors import LeaseLost, Problem
from delta_platform.worker.main import process_one


@pytest.fixture
def config():
    cert = ssl.DER_cert_to_PEM_cert(ssl.create_default_context().get_ca_certs(binary_form=True)[0])
    target = OnpremTarget(
        api_url="https://10.40.0.10:6443",
        ca_base64=base64.b64encode(cert.encode()).decode(),
        node_name="onprem",
        node_ip="10.40.0.10",
        allowed_image_prefixes=[REPOSITORY + "@"],
    )
    return aws_config().model_copy(
        update={
            "onprem_sandbox_target": target.model_dump_json(),
            "onprem_sandbox_token": SecretStr("test-only-controller"),
        }
    )


class Runtime(K3s):
    def __init__(self, config):
        self.target = OnpremTarget.model_validate_json(config.onprem_sandbox_target)
        self.items = {"pods": {}, "secrets": {}}
        self.ecr = SimpleNamespace(
            get_authorization_token=lambda: {
                "authorizationData": [
                    {
                        "proxyEndpoint": "https://" + REPOSITORY.split("/", 1)[0],
                        "authorizationToken": "test-pull-only",
                    }
                ]
            }
        )
        self.creates, self.fail_cleanup, self.lose_create, self.cancel = 0, False, False, None

    def call(self, method, collection, name=None, body=None, **kwargs):
        if method == "POST":
            item = copy.deepcopy(body)
            item["metadata"]["uid"] = str(uuid4())
            self.items[collection][item["metadata"]["name"]] = item
            if collection == "pods":
                self.creates += 1
                if self.lose_create:
                    raise LeaseLost()
            return item
        if method == "GET":
            return self.items[collection].get(name)
        if method == "DELETE":
            if self.fail_cleanup:
                raise ConnectionError("private error details")
            assert body["preconditions"]["uid"] == self.items[collection][name]["metadata"]["uid"]
            del self.items[collection][name]

    def verify(self, run_id, commit, image, path, guard):
        if self.cancel:
            self.cancel()
        guard()
        pod = self.items["pods"][self.target.name(run_id)]
        self.owned(pod, run_id, commit, image)
        return {
            "resource": self.target.resource(run_id),
            "revision": pod["metadata"]["uid"],
            "url": self.target.url,
            "image_digest": DIGEST,
        }


def queued(store, data, config):
    build, _ = build_queued(store, data)
    process_one(store, reader(), build_runner(Provider()))
    run, _ = store.enqueue_sandbox(
        build["service_id"], build["id"], "onprem", sandbox_target(config, "onprem")
    )
    return run, build


def test_real_manifest_is_nonroot_bounded_without_credentials_or_host_mounts(config):
    p, run_id = Runtime(config), uuid4()
    p.create(run_id, "a" * 40, REPOSITORY + "@" + DIGEST, 3000, lambda: None)
    spec = p.items["pods"][p.target.name(run_id)]["spec"]
    assert spec["automountServiceAccountToken"] is False
    assert spec["securityContext"]["runAsNonRoot"] is True
    assert spec["containers"][0]["securityContext"]["readOnlyRootFilesystem"] is True
    assert spec["activeDeadlineSeconds"] == 900
    assert "test-pull-only" not in str(spec)
    assert all("hostPath" not in v for v in spec["volumes"])


def test_onprem_executes_fixed_digest_and_cleans_credentials(store, data, config):
    run, _ = queued(store, data, config)
    p = Runtime(config)
    process_one(store, reader(), sandbox=OnpremSandboxRunner(config, provider=p))
    value = store.run(run["id"])
    assert value["status"] == "succeeded"
    assert value["result"]["execution_cloud"] == "onprem"
    assert value["result"]["image_digest"] == DIGEST and value["result"]["cleanup"] == "stopped"
    assert p.items == {"pods": {}, "secrets": {}}
    assert "test-only-controller" not in str(value)


def test_create_response_loss_resumes_same_pod(store, data, config, engine):
    run, _ = queued(store, data, config)
    p = Runtime(config)
    p.lose_create = True
    process_one(store, reader(), sandbox=OnpremSandboxRunner(config, provider=p))
    assert store.run(run["id"])["status"] == "running"
    expire(engine, run["id"])
    process_one(store, reader(), sandbox=OnpremSandboxRunner(config, provider=p))
    assert store.run(run["id"])["status"] == "succeeded" and p.creates == 1
    assert not any(p.items.values())


def test_failed_cleanup_retains_slot_and_recovery_does_not_rerun(store, data, config, engine):
    run, _ = queued(store, data, config)
    p = Runtime(config)
    p.fail_cleanup = True
    process_one(store, reader(), sandbox=OnpremSandboxRunner(config, provider=p))
    assert store.run(run["id"])["status"] == "running"
    p.fail_cleanup = False
    expire(engine, run["id"])
    process_one(store, reader(), sandbox=OnpremSandboxRunner(config, provider=p))
    assert store.run(run["id"])["status"] == "succeeded" and p.creates == 1


def test_cancel_cleans_pod_and_registry_secret(store, data, config):
    run, _ = queued(store, data, config)
    p = Runtime(config)
    p.cancel = lambda: store.cancel_sandbox(run["id"])
    process_one(store, reader(), sandbox=OnpremSandboxRunner(config, provider=p))
    assert store.run(run["id"])["error"]["code"] == "sandbox_cancelled"
    assert not any(p.items.values())


def test_foreign_pod_is_not_deleted(store, data, config):
    run, _ = queued(store, data, config)
    p = Runtime(config)
    p.create(run["id"], "f" * 40, REPOSITORY + "@" + DIGEST, 3000, lambda: None)
    before = copy.deepcopy(p.items)
    process_one(store, reader(), sandbox=OnpremSandboxRunner(config, provider=p))
    assert store.run(run["id"])["status"] == "running" and p.items == before


def test_onprem_target_change_is_not_aws_fallback(store, data, config):
    run, build = queued(store, data, config)
    with pytest.raises(Problem) as error:
        store.enqueue_sandbox(
            build["service_id"], build["id"], "onprem", sandbox_target(aws_config())
        )
    assert error.value.code == "idempotency_conflict"
    assert store.run(run["id"])["status"] == "queued"


def test_changed_runtime_boundaries_are_rejected(config):
    p, run_id = Runtime(config), uuid4()
    image = REPOSITORY + "@" + DIGEST
    p.create(run_id, "a" * 40, image, 3000, lambda: None)
    original = p.items["pods"][p.target.name(run_id)]
    for field, value in (
        ("hostNetwork", True),
        ("automountServiceAccountToken", True),
        ("activeDeadlineSeconds", 9999),
        ("volumes", [{"name": "tmp", "hostPath": {"path": "/"}}]),
    ):
        changed = copy.deepcopy(original)
        changed["spec"][field] = value
        with pytest.raises(SandboxError):
            p.owned(changed, run_id, "a" * 40, image)
    changed = copy.deepcopy(original)
    changed["spec"]["containers"][0]["securityContext"]["readOnlyRootFilesystem"] = False
    with pytest.raises(SandboxError):
        p.owned(changed, run_id, "a" * 40, image)


def test_dast_binds_onprem_pod_uid_and_the_original_image(store, data, config):
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
    config = dast_config().model_copy(
        update={
            "onprem_sandbox_target": config.onprem_sandbox_target,
            "onprem_sandbox_token": config.onprem_sandbox_token,
        }
    )
    run, _ = store.enqueue_sandbox(
        build["service_id"],
        build["id"],
        "onprem-dast",
        sandbox_target(config, "onprem"),
        security_check_id=security["id"],
        dast_target=dast_target(config),
    )
    remote, provider = Dast(), Runtime(config)
    inspector = DastInspector(
        config,
        transport=httpx.MockTransport(remote),
        sast_transport=httpx.MockTransport(sast),
        poll_seconds=0,
    )
    process_one(
        store, reader(), dast=OnpremSandboxRunner(config, provider=provider, inspector=inspector)
    )
    result = store.run(run["id"])
    assert result["status"] == "succeeded" and not any(provider.items.values())
    binding = remote.inputs[str(run["id"])]["binding"]
    assert binding["execution_cloud"] == "onprem" and binding["target_url"] == provider.target.url
    assert binding["runtime_revision"] == result["result"]["revision"]
    assert binding["runtime_resource"] == provider.target.resource(run["id"])
    assert binding["image"] == REPOSITORY + "@" + DIGEST
    assert remote.stops == [str(run["id"])]
