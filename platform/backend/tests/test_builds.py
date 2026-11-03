from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from support import LOGIN_ID, PASSWORD, settings
from test_worker import expire, reader

from delta_platform.adapters.builds import CodeBuildRunner, build_target
from delta_platform.api.app import create_app
from delta_platform.core.errors import Problem
from delta_platform.core.schema import runs
from delta_platform.worker.main import process_one

PROJECT = "delta-platform-builds"
REPOSITORY = "111122223333.dkr.ecr.ap-northeast-2.amazonaws.com/delta-platform-builds"
DIGEST = "sha256:" + "b" * 64


def config():
    return settings().model_copy(update={"build_project": PROJECT, "build_repository": REPOSITORY})


def queued(store, data, key="build"):
    service, _ = store.register(data, "service")
    source, _ = store.enqueue(service["id"], "source")
    process_one(store, reader())
    return store.enqueue_build(service["id"], source["id"], key, build_target(config()))[0], source


class Provider:
    def __init__(self, state="SUCCEEDED", lost_reply=False):
        self.state, self.lost_reply = state, lost_reply
        self.starts, self.builds, self.digests = [], {}, []

    def start_build(self, **request):
        self.starts.append(request)
        identifier = PROJECT + ":" + request["idempotencyToken"]
        self.builds[identifier] = {
            "id": identifier,
            "projectName": PROJECT,
            "buildStatus": self.state,
            "currentPhase": "COMPLETED",
            "sourceVersion": request["sourceVersion"],
            "resolvedSourceVersion": request["sourceVersion"],
            "environment": {"environmentVariables": request["environmentVariablesOverride"]},
            "logs": {"groupName": "/aws/codebuild/" + PROJECT, "streamName": "test"},
            "phases": [{"phaseType": "BUILD", "phaseStatus": self.state}],
        }
        if self.lost_reply:
            self.lost_reply = False
            raise ConnectionError("lost reply")
        return {"build": self.builds[identifier]}

    def batch_get_builds(self, ids):
        return {"builds": [self.builds[i] for i in ids]}

    def describe_images(self, **kwargs):
        self.digests.append(kwargs)
        return {
            "imageDetails": [
                {
                    "imageDigest": DIGEST,
                    "imageManifestMediaType": "application/vnd.oci.image.manifest.v1+json",
                }
            ]
        }

    def get_log_events(self, **kwargs):
        return {"events": [{"message": "\x1b[31m<script>untrusted</script>\x1b[0m"}]}


def runner(provider):
    return CodeBuildRunner(
        config(), codebuild=provider, ecr=provider, logs=provider, sleep=lambda _: None
    )


def test_build_pins_source_digest_and_keeps_u1_history(store, data):
    run, source = queued(store, data)
    p = Provider()
    process_one(store, reader(), runner(p))
    result = store.run(run["id"])
    assert result["status"] == "succeeded"
    assert result["result"]["image"] == REPOSITORY + "@" + DIGEST
    assert result["result"]["platform"] == "linux/amd64"
    assert p.starts[0]["sourceVersion"] == result["resolved_commit_sha"]
    assert "buildspecOverride" not in p.starts[0]
    assert p.digests[0]["imageIds"] == [{"imageTag": f"run-{run['id']}"}]
    assert store.service(run["service_id"])["latest_source_check"]["id"] == source["id"]
    assert len(store.list_runs(run["service_id"], 100, None)["items"]) == 1
    again, created = store.enqueue_build(
        run["service_id"], source["id"], "build", build_target(config())
    )
    assert not created and again["id"] == run["id"]
    assert runner(p).log_tail(result)["text"] == "<script>untrusted</script>"


def test_rejects_unchecked_source_and_conflicting_jobs(store, data):
    run, source = queued(store, data)
    with pytest.raises(Problem) as exc:
        store.enqueue_build(run["service_id"], uuid4(), "invalid", build_target(config()))
    assert exc.value.code == "source_check_required"
    with pytest.raises(Problem) as exc:
        store.enqueue_build(run["service_id"], source["id"], "other", build_target(config()))
    assert exc.value.code == "build_in_progress"
    with pytest.raises(Problem):
        store.enqueue(run["service_id"], "source-again")
    with pytest.raises(Problem) as exc:
        store.enqueue_build(run["service_id"], uuid4(), "build", build_target(config()))
    assert exc.value.code == "idempotency_conflict"


def test_reclaims_external_execution_without_resubmitting(store, data, engine):
    run, _ = queued(store, data)
    claim = store.claim()
    p = Provider()
    r = runner(p)
    request = r.request(claim)
    store.prepare_submission(claim["id"], claim["lease_token"], request)
    external_id = p.start_build(**request)["build"]["id"]
    store.attach_build(claim["id"], claim["lease_token"], external_id)
    expire(engine, run["id"])
    process_one(store, reader(), r)
    assert store.run(run["id"])["status"] == "succeeded"
    assert len(p.starts) == 1


def test_lost_start_reply_reuses_identical_token_and_request(store, data, engine):
    run, _ = queued(store, data)
    p = Provider(lost_reply=True)
    process_one(store, reader(), runner(p))
    expire(engine, run["id"])
    process_one(store, reader(), runner(p))
    assert store.run(run["id"])["status"] == "succeeded"
    assert len(p.builds) == 1 and p.starts[0] == p.starts[1]


def test_expired_start_intent_never_resubmits(store, data, engine):
    run, _ = queued(store, data)
    claim = store.claim()
    p = Provider()
    submission = {
        "prepared_at": (datetime.now(UTC) - timedelta(minutes=6)).isoformat(),
        "request": runner(p).request(claim),
    }
    with engine.begin() as conn:
        conn.execute(runs.update().where(runs.c.id == run["id"]).values(submission=submission))
    expire(engine, run["id"])
    process_one(store, reader(), runner(p))
    assert store.run(run["id"])["error"]["code"] == "build_submission_uncertain"
    assert not p.starts


@pytest.mark.parametrize("status", ["FAILED", "TIMED_OUT", "FAULT", "STOPPED"])
def test_failed_build_does_not_publish_an_image(store, data, status):
    run, _ = queued(store, data)
    p = Provider(status)
    process_one(store, reader(), runner(p))
    result = store.run(run["id"])
    assert result["status"] == "failed" and result["result"] is None
    assert result["error"]["code"] == "build_" + status.lower()
    assert not p.digests


def test_commit_mismatch_does_not_publish_result(store, data):
    run, _ = queued(store, data)
    p = Provider()
    original = p.batch_get_builds

    def changed(ids):
        result = original(ids)
        result["builds"][0]["resolvedSourceVersion"] = "c" * 40
        return result

    p.batch_get_builds = changed
    process_one(store, reader(), runner(p))
    assert store.run(run["id"])["error"]["code"] == "build_commit_mismatch"


def test_api_requires_auth_csrf_and_successful_source(engine, store, data):
    run, source = queued(store, data)
    with TestClient(create_app(config(), engine), base_url="http://127.0.0.1:8088") as client:
        assert client.get(f"/api/v1/builds/{run['id']}").status_code == 401
        client.headers.update({"Origin": "http://127.0.0.1:8088", "X-Platform-Request": "1"})
        login = client.post("/api/v1/auth/login", json={"login_id": LOGIN_ID, "password": PASSWORD})
        url = f"/api/v1/services/{run['service_id']}/builds"
        assert client.post(url, json={"source_check_id": str(source["id"])}).status_code == 403
        client.headers.update(
            {"X-CSRF-Token": login.json()["csrf_token"], "Idempotency-Key": "build"}
        )
        response = client.post(url, json={"source_check_id": str(source["id"])})
        assert response.status_code == 200 and response.json()["id"] == str(run["id"])
        assert client.post(url, json={"source_check_id": "-" * 36}).status_code == 422
        assert client.get(f"/api/v1/source-checks/{run['id']}").status_code == 404
        assert client.get(f"/api/v1/builds/{source['id']}").status_code == 404
        assert len(client.get(url).json()["items"]) == 1


def test_access_denied_reports_configuration_error(store, data):
    from botocore.exceptions import ClientError

    run, _ = queued(store, data)
    p = Provider()

    def denied(**request):
        raise ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "never expose this"}},
            "StartBuild",
        )

    p.start_build = denied
    process_one(store, reader(), runner(p))
    result = store.run(run["id"])
    assert result["status"] == "failed"
    assert result["error"]["provider_code"] == "AccessDeniedException"
    assert "never expose" not in str(result)


def test_build_requests_are_serialized_and_keys_are_kind_scoped(store, data):
    from concurrent.futures import ThreadPoolExecutor

    service, _ = store.register(data, "service")
    source, _ = store.enqueue(service["id"], "same-key")
    process_one(store, reader())
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _: store.enqueue_build(
                    service["id"], source["id"], "same-key", build_target(config())
                ),
                range(4),
            )
        )
    assert len({r[0]["id"] for r in results}) == 1
    assert sum(r[1] for r in results) == 1
    assert results[0][0]["id"] != source["id"]


def test_other_services_preflight_cannot_be_built(store, data):
    run, source = queued(store, data)
    other, _ = store.register(data.model_copy(update={"name": "another"}), "other")
    with pytest.raises(Problem) as exc:
        store.enqueue_build(other["id"], source["id"], "invalid", build_target(config()))
    assert exc.value.code == "source_check_required"


def test_builder_not_configured_is_not_a_fake_success(signed, data, store):
    service, _ = store.register(data, "service")
    response = signed.post(
        f"/api/v1/services/{service['id']}/builds",
        json={"source_check_id": str(uuid4())},
        headers={"Idempotency-Key": "build"},
    )
    assert (
        response.status_code == 503 and response.json()["error"]["code"] == "builder_not_configured"
    )
    assert store.list_runs(service["id"], 100, None, "image_build")["items"] == []
