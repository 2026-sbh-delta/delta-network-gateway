import json
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from support import settings
from test_builds import DIGEST, Provider
from test_builds import queued as queued_build
from test_builds import runner as builder
from test_worker import expire, reader

from delta_platform.adapters.security import SecurityRunner, security_target
from delta_platform.core.errors import Problem
from delta_platform.worker.main import process_one


def config():
    return settings().model_copy(
        update={
            "sast_url": "http://127.0.0.1:18790",
            "sast_token": SecretStr("test-only-sast-token"),
        }
    )


def queued(store, data):
    build, _ = queued_build(store, data)
    process_one(store, reader(), builder(Provider()))
    run, _ = store.enqueue_security(
        build["service_id"], build["id"], "security", security_target(config())
    )
    return run, build


class Sast:
    def __init__(self, *, kinds=("fail", "review"), bad_commit=False, lost=False):
        self.kinds, self.bad_commit, self.lost = kinds, bad_commit, lost
        self.requests = []
        self.inputs = {}

    def __call__(self, request):
        assert request.headers["Authorization"] == "Bearer test-only-sast-token"
        self.requests.append(request)
        if request.method == "POST":
            body = json.loads(request.content)
            if body["run_id"] in self.inputs:
                assert self.inputs[body["run_id"]] == body
            self.inputs[body["run_id"]] = body
            if self.lost:
                self.lost = False
                raise httpx.ReadTimeout("never print token", request=request)
            return httpx.Response(
                202,
                json={
                    **body,
                    "commit": "b" * 40 if self.bad_commit else body["commit"],
                    "state": "done",
                },
            )
        if request.url.path.endswith("/sarif"):
            return httpx.Response(
                200,
                json={
                    "version": "2.1.0",
                    "runs": [
                        {
                            "results": [
                                {
                                    "kind": k,
                                    "ruleId": "delta.xss",
                                    "message": {"text": "untrusted secret text"},
                                    "locations": [
                                        {
                                            "physicalLocation": {
                                                "artifactLocation": {"uri": "app.py"},
                                                "region": {"startLine": 12},
                                            }
                                        }
                                    ],
                                }
                                for k in self.kinds
                            ]
                        }
                    ],
                },
            )
        raise AssertionError(str(request.url))


def runner(sast):
    return SecurityRunner(config(), transport=httpx.MockTransport(sast), sleep=lambda _: None)


def test_sast_binds_evidence_to_build_without_deployment_approval(store, data):
    run, build = queued(store, data)
    remote = Sast()
    process_one(store, reader(), security=runner(remote))
    current = store.run(run["id"])
    assert current["status"] == "succeeded"
    result = current["result"]
    assert result["build_id"] == str(build["id"]) and result["image_digest"] == DIGEST
    assert result["counts"]["fail"] == result["counts"]["review"] == 1
    assert result["requires_review"] and not result["deployment_approved"]
    assert result["dast_check"] == "not_run" and len(result["evidence_sha256"]) == 64
    assert "untrusted secret text" not in json.dumps(result)
    assert remote.inputs[str(run["id"])]["commit"] == current["resolved_commit_sha"]


def test_zero_findings_is_not_full_security_approval(store, data):
    run, _ = queued(store, data)
    process_one(store, reader(), security=runner(Sast(kinds=())))
    result = store.run(run["id"])["result"]
    assert not result["requires_review"] and not result["deployment_approved"]
    assert result["coverage"] == "configured_scanner_rules_only"


def test_sast_lost_reply_reuses_same_id(store, data, engine):
    run, _ = queued(store, data)
    remote = Sast(lost=True)
    process_one(store, reader(), security=runner(remote))
    expire(engine, run["id"])
    process_one(store, reader(), security=runner(remote))
    assert store.run(run["id"])["status"] == "succeeded" and len(remote.inputs) == 1


def test_wrong_commit_never_fetches_or_accepts_report(store, data):
    run, _ = queued(store, data)
    remote = Sast(bad_commit=True)
    process_one(store, reader(), security=runner(remote))
    assert store.run(run["id"])["error"]["code"] == "security_identity_mismatch"
    assert len(remote.requests) == 1


@pytest.mark.parametrize(
    "report",
    [
        {},
        {"version": "2.1.0", "runs": []},
        {
            "version": "2.1.0",
            "runs": [{"results": [], "invocations": [{"executionSuccessful": False}]}],
        },
        {"version": "2.1.0", "runs": [{"results": None}]},
    ],
)
def test_missing_evidence_is_not_a_pass(report):
    from delta_platform.adapters.builds import BuildError

    with pytest.raises(BuildError):
        SecurityRunner.summarize(report)


def test_unknown_kind_requires_review_and_absolute_paths_are_hidden():
    value = SecurityRunner.summarize(
        {
            "version": "2.1.0",
            "runs": [
                {
                    "results": [
                        {
                            "kind": "unexpected",
                            "locations": [
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {"uri": "/private/credential"},
                                        "region": {"startLine": 1},
                                    }
                                }
                            ],
                        }
                    ]
                }
            ],
        }
    )
    assert value["counts"]["review"] == 1 and value["findings"][0]["file"] == "경로 생략"


def test_other_services_build_and_conflicting_jobs_are_rejected(store, data):
    run, build = queued(store, data)
    other, _ = store.register(data.model_copy(update={"name": "other"}), "other")
    with pytest.raises(Problem) as exc:
        store.enqueue_security(other["id"], build["id"], "x", security_target(config()))
    assert exc.value.code == "successful_build_required"
    with pytest.raises(Problem):
        store.enqueue_security(
            build["service_id"], build["id"], "different", security_target(config())
        )
    same, created = store.enqueue_security(
        build["service_id"], build["id"], "security", security_target(config())
    )
    assert not created and same["id"] == run["id"]


def test_unconfigured_sast_does_not_create_job(signed, store, data):
    build, _ = queued_build(store, data)
    response = signed.post(
        f"/api/v1/services/{build['service_id']}/security-checks",
        json={"build_id": str(build["id"])},
        headers={"Idempotency-Key": "security"},
    )
    assert response.status_code == 503
    assert store.list_runs(build["service_id"], 50, None, "security_check")["items"] == []


def test_security_routes_require_auth_and_do_not_expose_private_snapshot(engine, store, data):
    from fastapi.testclient import TestClient
    from support import LOGIN_ID, PASSWORD

    from delta_platform.api.app import create_app

    run, build = queued(store, data)
    with TestClient(create_app(config(), engine), base_url="http://127.0.0.1:8088") as client:
        assert client.get(f"/api/v1/security-checks/{run['id']}").status_code == 401
        client.headers.update({"Origin": "http://127.0.0.1:8088", "X-Platform-Request": "1"})
        auth = client.post("/api/v1/auth/login", json={"login_id": LOGIN_ID, "password": PASSWORD})
        path = f"/api/v1/services/{build['service_id']}/security-checks"
        assert client.post(path, json={"build_id": str(build["id"])}).status_code == 403
        client.headers.update(
            {"X-CSRF-Token": auth.json()["csrf_token"], "Idempotency-Key": "security"}
        )
        response = client.post(path, json={"build_id": str(build["id"])})
        assert response.status_code == 200 and response.json()["id"] == str(run["id"])
        assert "input_snapshot" not in response.json()
        assert client.get(f"/api/v1/builds/{run['id']}").status_code == 404
        assert client.get(f"/api/v1/security-checks/{uuid4()}").status_code == 404


def test_security_migration_preserves_existing_builds(store, data):
    from alembic import command
    from alembic.config import Config

    build, _ = queued_build(store, data)
    before = store.run(build["id"])
    config = Config("alembic.ini")
    try:
        command.downgrade(config, "0003")
        command.upgrade(config, "0004")
        assert store.run(build["id"]) == before
    finally:
        command.upgrade(config, "head")


def test_security_records_block_unsafe_downgrade(store, data, engine):
    from alembic import command
    from alembic.config import Config

    from delta_platform.core.database import ready

    run, _ = queued(store, data)
    with pytest.raises(RuntimeError, match="자동 downgrade"):
        command.downgrade(Config("alembic.ini"), "0003")
    assert ready(engine) and store.run(run["id"])["kind"] == "security_check"
