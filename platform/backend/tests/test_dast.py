import copy
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from support import LOGIN_ID, PASSWORD
from test_sandbox import Ecs
from test_sandbox import config as sandbox_config
from test_security import Sast
from test_security import config as security_config
from test_security import queued as security_queued
from test_security import runner as security_runner
from test_worker import expire, reader

from delta_platform.adapters.dast import DastInspector, DastRunner, dast_target
from delta_platform.adapters.sandbox import sandbox_target
from delta_platform.api.app import create_app
from delta_platform.core.errors import Problem
from delta_platform.worker.main import process_one


def config():
    security = security_config()
    return sandbox_config().model_copy(
        update={
            "sast_url": security.sast_url,
            "sast_token": security.sast_token,
            "dast_url": "http://127.0.0.1:18765",
            "dast_token": SecretStr("test-only-dast-token"),
        }
    )


def enqueue(store, service_id, build_id, security_id, key="dast"):
    return store.enqueue_sandbox(
        service_id,
        build_id,
        key,
        sandbox_target(config()),
        security_check_id=security_id,
        dast_target=dast_target(config()),
    )


def queued(store, data):
    security, build = security_queued(store, data)
    sast = Sast()
    process_one(store, reader(), security=security_runner(sast))
    run, _ = enqueue(store, build["service_id"], build["id"], security["id"])
    return run, build, security, sast


class Dast:
    def __init__(self, mode="done", *, provider=None):
        self.mode = mode
        self.inputs, self.stops = {}, []
        self.provider = provider
        self.stop_ok = True
        self.lost = False
        self.on_start = None

    def __call__(self, request):
        assert request.headers["Authorization"] == "Bearer test-only-dast-token"
        run_id = request.url.path.split("/")[-2 if request.url.path.endswith("/cancel") else -1]
        if request.url.path.endswith("/cancel"):
            self.stops.append(run_id)
            return httpx.Response(
                200 if self.stop_ok else 503, json={"run_id": run_id, "stopped": self.stop_ok}
            )
        if request.method == "PUT":
            assert self.provider is None or self.provider.count == 1
            body = json.loads(request.content)
            if run_id in self.inputs:
                assert body == self.inputs[run_id]
            self.inputs[run_id] = body
            if self.on_start:
                self.on_start()
            if self.lost:
                self.lost = False
                raise httpx.ReadTimeout("do not expose private token")
        binding = copy.deepcopy(self.inputs[run_id]["binding"])
        if self.mode == "mismatch":
            binding["image_digest"] = "sha256:" + "f" * 64
        skipped = self.mode == "skipped"
        return httpx.Response(
            200,
            json={
                "run_id": run_id,
                "binding": binding,
                "status": "skipped" if skipped else "running" if self.mode == "running" else "done",
                "eligible_count": 0 if skipped else 1,
                "finding_ids": [] if skipped else ["sast-test"],
                "findings_truncated": False,
                "results": []
                if skipped
                else [
                    {"finding_id": "sast-test", "status": "REPRODUCED", "evidence_sha256": "c" * 64}
                ],
                "evidence_sha256": "e" * 64,
                "coverage": "sast_xss_candidates_only",
                "deployment_approved": False,
            },
        )


def runner(provider, remote, sast):
    return DastRunner(
        config(),
        inspector=DastInspector(
            config(),
            transport=httpx.MockTransport(remote),
            sast_transport=httpx.MockTransport(sast),
            poll_seconds=0,
        ),
        clients={k: provider for k in ("ecs", "sts", "elbv2", "ec2")},
        poll_seconds=0,
        timeout=0,
    )


def test_actual_workflow_keeps_app_alive_until_dast_and_then_cleans(store, data):
    run, build, security, sast = queued(store, data)
    p = Ecs()
    remote = Dast(provider=p)
    process_one(store, reader(), dast=runner(p, remote, sast))
    value = store.run(run["id"])
    assert value["status"] == "succeeded"
    assert p.count == 0 and remote.stops == [str(run["id"])]
    result = value["result"]
    assert result["build_id"] == str(build["id"]) and result["security_check_id"] == str(
        security["id"]
    )
    assert result["dast_counts"]["REPRODUCED"] == 1 and result["requires_review"]
    assert result["deployment_approved"] is False and result["cleanup"] == "stopped"


@pytest.mark.parametrize(
    "mode,code", [("mismatch", "dast_identity_mismatch"), ("running", "sandbox_cancelled")]
)
def test_wrong_target_or_cancel_never_returns_success_and_cleans(store, data, mode, code):
    run, _, _, sast = queued(store, data)
    p = Ecs()
    remote = Dast(mode, provider=p)
    if mode == "running":
        remote.on_start = lambda: store.cancel_sandbox(run["id"], "dast_check")
    process_one(store, reader(), dast=runner(p, remote, sast))
    assert store.run(run["id"])["error"]["code"] == code
    assert p.count == 0 and remote.stops


def test_lost_dast_reply_recovers_by_id_without_duplicate(store, data):
    run, _, _, sast = queued(store, data)
    p = Ecs()
    remote = Dast(provider=p)
    remote.lost = True
    process_one(store, reader(), dast=runner(p, remote, sast))
    assert store.run(run["id"])["status"] == "succeeded"
    assert len(remote.inputs) == 1 and p.count == 0


def test_unknown_remote_cleanup_stops_app_but_holds_slot_until_confirmed(store, data, engine):
    run, build, security, sast = queued(store, data)
    p = Ecs()
    remote = Dast(provider=p)
    remote.stop_ok = False
    process_one(store, reader(), dast=runner(p, remote, sast))
    assert p.count == 0 and store.run(run["id"])["status"] == "running"
    with pytest.raises(Problem):
        store.enqueue_sandbox(build["service_id"], build["id"], "sandbox", sandbox_target(config()))
    remote.stop_ok = True
    expire(engine, run["id"])
    process_one(store, reader(), dast=runner(p, remote, sast))
    assert store.run(run["id"])["status"] == "succeeded"
    assert len(remote.inputs) == 1
    assert [u["desiredCount"] for u in p.updates] == [1, 0]


def test_worker_restart_recovers_same_revision_and_inspection(store, data, engine):
    run, _, _, sast = queued(store, data)
    p = Ecs()
    p.interrupt_start = True
    remote = Dast(provider=p)
    process_one(store, reader(), dast=runner(p, remote, sast))
    revision = store.run(run["id"])["external_id"]
    expire(engine, run["id"])
    process_one(store, reader(), dast=runner(p, remote, sast))
    assert store.run(run["id"])["external_id"] == revision
    assert store.run(run["id"])["status"] == "succeeded" and p.count == 0
    assert len(p.definitions) == 2


def test_failed_app_cleanup_keeps_pool_locked_until_recovery(store, data, engine):
    run, build, _, sast = queued(store, data)
    p = Ecs()
    p.cleanup_error = True
    remote = Dast(provider=p)
    process_one(store, reader(), dast=runner(p, remote, sast))
    assert p.count == 1 and store.run(run["id"])["status"] == "running"
    with pytest.raises(Problem):
        store.enqueue_sandbox(build["service_id"], build["id"], "blocked", sandbox_target(config()))
    p.cleanup_error = False
    expire(engine, run["id"])
    process_one(store, reader(), dast=runner(p, remote, sast))
    assert p.count == 0 and store.run(run["id"])["status"] == "succeeded"
    assert len(remote.inputs) == 1


def test_missing_runner_configuration_does_not_release_active_slot(store, data, monkeypatch):
    import delta_platform.worker.main as worker

    run, _, _, _ = queued(store, data)

    def missing(_):
        raise Problem("dast_not_configured", "not configured", 503)

    monkeypatch.setattr(worker, "DastRunner", missing)
    process_one(store, reader())
    assert store.run(run["id"])["status"] == "running"
    assert store.run(run["id"])["current_step"] == "dast_cleanup_wait"


def test_no_candidate_is_explicitly_not_run(store, data):
    run, _, _, sast = queued(store, data)
    p = Ecs()
    remote = Dast("skipped", provider=p)
    process_one(store, reader(), dast=runner(p, remote, sast))
    result = store.run(run["id"])["result"]
    assert result["dast_check"] == "not_run" and result["requires_review"]
    assert not result["deployment_approved"] and p.count == 0


def test_changed_sarif_is_rejected_before_dast_submit(store, data):
    run, _, _, sast = queued(store, data)
    sast.kinds = ()
    p = Ecs()
    remote = Dast(provider=p)
    process_one(store, reader(), dast=runner(p, remote, sast))
    assert store.run(run["id"])["error"]["code"] == "dast_sast_changed"
    assert not remote.inputs and p.count == 0


def test_admission_requires_matching_sast_and_is_idempotent(store, data):
    run, build, security, _ = queued(store, data)
    same, created = enqueue(store, build["service_id"], build["id"], security["id"])
    assert not created and same["id"] == run["id"]
    store.cancel_sandbox(run["id"], "dast_check")
    with pytest.raises(Problem) as exc:
        enqueue(store, build["service_id"], build["id"], uuid4(), "bad")
    assert exc.value.code == "dast_source_mismatch"


def test_dast_and_runtime_checks_from_different_services_share_one_slot(store, data):
    from test_builds import Provider, build_target
    from test_builds import runner as build_runner

    security, first = security_queued(store, data)
    process_one(store, reader(), security=security_runner(Sast()))
    service, _ = store.register(data.model_copy(update={"name": "second"}), "second")
    source, _ = store.enqueue(service["id"], "source")
    process_one(store, reader())
    second, _ = store.enqueue_build(service["id"], source["id"], "build", build_target(config()))
    process_one(store, reader(), build_runner(Provider()))

    def submit(kind):
        try:
            if kind == "dast":
                enqueue(store, first["service_id"], first["id"], security["id"])
            else:
                store.enqueue_sandbox(
                    service["id"], second["id"], "sandbox", sandbox_target(config())
                )
            return "accepted"
        except Problem as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(submit, ("dast", "sandbox"))) == ["accepted", "sandbox_busy"]


def test_http_auth_csrf_and_private_snapshot(store, data, engine):
    run, build, security, _ = queued(store, data)
    with TestClient(create_app(config(), engine), base_url="http://127.0.0.1:8088") as client:
        path = f"/api/v1/services/{build['service_id']}/dast-checks"
        assert client.get(path).status_code == 401
        client.headers.update({"Origin": "http://127.0.0.1:8088", "X-Platform-Request": "1"})
        auth = client.post("/api/v1/auth/login", json={"login_id": LOGIN_ID, "password": PASSWORD})
        body = {"build_id": str(build["id"]), "security_check_id": str(security["id"])}
        assert client.post(path, json=body).status_code == 403
        client.headers.update(
            {"X-CSRF-Token": auth.json()["csrf_token"], "Idempotency-Key": "dast"}
        )
        response = client.post(path, json=body)
        assert response.status_code == 200 and response.json()["id"] == str(run["id"])
        assert "input_snapshot" not in response.json()
        assert client.get(f"/api/v1/security-checks/{run['id']}").status_code == 404


def test_migration_preserves_records_and_blocks_dast_downgrade(store, data, engine):
    from alembic import command
    from alembic.config import Config

    from delta_platform.core.database import ready

    run, _, _, _ = queued(store, data)
    with pytest.raises(RuntimeError, match="자동 downgrade"):
        command.downgrade(Config("alembic.ini"), "0004")
    assert ready(engine) and store.run(run["id"])["kind"] == "dast_check"
