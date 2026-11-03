import json

import httpx
import pytest
from support import settings
from test_builds import DIGEST, REPOSITORY, Provider
from test_worker import expire, reader

from delta_platform.adapters.builds import build_runner, build_target
from delta_platform.adapters.jenkins import JenkinsRunner
from delta_platform.core.errors import Problem
from delta_platform.worker.main import process_one


def config():
    from pydantic import SecretStr

    return settings().model_copy(
        update={
            "build_repository": REPOSITORY,
            "jenkins_url": "http://127.0.0.1:18081",
            "jenkins_user": "platform",
            "jenkins_token": SecretStr("api-secret-for-tests"),
        }
    )


def queued(store, data):
    service, _ = store.register(data, "service")
    source, _ = store.enqueue(service["id"], "source")
    process_one(store, reader())
    return store.enqueue_build(service["id"], source["id"], "build", build_target(config()))[0]


class Jenkins:
    def __init__(self, *, lost_reply=False, state="SUCCESS", wrong_result=False):
        self.parameters = None
        self.posts = 0
        self.lost_reply, self.state, self.wrong_result = lost_reply, state, wrong_result
        self.requests = []

    def actions(self):
        return [{"parameters": [{"name": k, "value": v} for k, v in self.parameters.items()]}]

    def __call__(self, request):
        from urllib.parse import parse_qs

        self.requests.append(request)
        assert request.url.host == "127.0.0.1"
        if request.method == "POST":
            self.posts += 1
            self.parameters = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            if self.lost_reply:
                self.lost_reply = False
                raise httpx.ReadTimeout("never expose upstream secret", request=request)
            return httpx.Response(201, headers={"Location": "http://127.0.0.1:18081/queue/item/7/"})
        path = request.url.path
        if path == "/queue/item/7/api/json":
            return httpx.Response(
                200, json={"id": 7, "actions": self.actions(), "executable": {"number": 3}}
            )
        if path == "/job/delta-platform-image/api/json":
            return httpx.Response(
                200,
                json={
                    "builds": []
                    if self.parameters is None
                    else [{"number": 3, "actions": self.actions()}]
                },
            )
        if path == "/queue/api/json":
            return httpx.Response(200, json={"items": []})
        if path.endswith("/3/api/json"):
            return httpx.Response(
                200,
                json={
                    "number": 3,
                    "building": False,
                    "result": self.state,
                    "actions": self.actions(),
                },
            )
        if path.endswith("/artifact/result.json"):
            return httpx.Response(
                200,
                json={
                    "schema_version": 1,
                    "run_id": self.parameters["DELTA_RUN_ID"],
                    "commit_sha": "0" * 40
                    if self.wrong_result
                    else self.parameters["DELTA_COMMIT"],
                    "image": REPOSITORY + "@" + DIGEST,
                    "image_digest": DIGEST,
                    "platform": "linux/amd64",
                },
            )
        if path.endswith("/consoleText"):
            return httpx.Response(
                200, content=b"\x1b[31m<script>text</script> api-secret-for-tests\x00"
            )
        raise AssertionError(str(request.url))


def runner(jenkins, ecr=None):
    return JenkinsRunner(
        config(),
        transport=httpx.MockTransport(jenkins),
        ecr=ecr or Provider(),
        sleep=lambda _: None,
    )


def test_jenkins_image_can_feed_existing_sandbox_contract(store, data):
    run = queued(store, data)
    remote = Jenkins()
    process_one(store, reader(), runner(remote))
    result = store.run(run["id"])
    assert result["status"] == "succeeded"
    assert result["external_id"] == "jenkins:build:3"
    assert result["result"]["image"] == REPOSITORY + "@" + DIGEST
    assert result["result"]["provider"] == "jenkins"
    assert remote.posts == 1
    assert "Jenkinsfile" not in json.dumps(remote.parameters)
    logs = runner(remote).log_tail(store.build_execution(run["id"]))
    assert logs["text"] == "<script>text</script> [REDACTED]"
    from test_sandbox import config as sandbox_config

    from delta_platform.adapters.sandbox import sandbox_target

    check, created = store.enqueue_sandbox(
        run["service_id"], run["id"], "sandbox", sandbox_target(sandbox_config())
    )
    claim = store.claim()
    assert created and claim["id"] == check["id"]
    assert claim["input_snapshot"]["image_digest"] == DIGEST


def test_logs_route_reads_private_context_without_exposing_snapshot(
    store, data, signed, monkeypatch
):
    run = queued(store, data)
    remote = Jenkins()
    process_one(store, reader(), runner(remote))
    monkeypatch.setattr("delta_platform.api.app.build_runner", lambda settings, row: runner(remote))
    response = signed.get(f"/api/v1/builds/{run['id']}/logs")
    assert response.status_code == 200 and response.json()["available"]
    public = signed.get(f"/api/v1/builds/{run['id']}").json()
    assert "input_snapshot" not in public and "submission" not in public


def test_lost_reply_recovers_without_second_post(store, data, engine):
    run = queued(store, data)
    remote = Jenkins(lost_reply=True)
    process_one(store, reader(), runner(remote))
    assert store.run(run["id"])["status"] == "running"
    expire(engine, run["id"])
    process_one(store, reader(), runner(remote))
    assert store.run(run["id"])["status"] == "succeeded"
    assert remote.posts == 1


def test_unknown_submission_does_not_submit(store, data, engine):
    run = queued(store, data)
    claim = store.claim()
    remote = Jenkins()
    store.prepare_submission(claim["id"], claim["lease_token"], runner(remote).parameters(claim))
    expire(engine, run["id"])
    process_one(store, reader(), runner(remote))
    assert store.run(run["id"])["error"]["code"] == "build_submission_uncertain"
    assert remote.posts == 0


@pytest.mark.parametrize("state", ["FAILURE", "ABORTED", "UNSTABLE", None])
def test_unsuccessful_jenkins_never_publishes_image(store, data, state):
    run = queued(store, data)
    ecr = Provider()
    process_one(store, reader(), runner(Jenkins(state=state), ecr))
    assert store.run(run["id"])["status"] == "failed"
    assert not ecr.digests


def test_wrong_commit_artifact_is_rejected(store, data):
    run = queued(store, data)
    ecr = Provider()
    process_one(store, reader(), runner(Jenkins(wrong_result=True), ecr))
    assert store.run(run["id"])["error"]["code"] == "build_result_invalid"
    assert not ecr.digests


def test_ecr_mismatch_is_not_success(store, data):
    run = queued(store, data)
    ecr = Provider()
    ecr.describe_images = lambda **kw: {"imageDetails": [{"imageDigest": "sha256:" + "c" * 64}]}
    process_one(store, reader(), runner(Jenkins(), ecr))
    assert store.run(run["id"])["error"]["code"] == "image_mismatch"


def test_redirect_never_receives_token(store, data):
    run = queued(store, data)
    calls = []

    def redirect(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": "https://attacker.test/steal"})

    process_one(store, reader(), runner(redirect))
    assert len(calls) == 1 and calls[0].url.host == "127.0.0.1"
    assert store.run(run["id"])["error"]["code"] == "jenkins_request_failed"


def test_other_run_is_rejected_before_artifact_or_logs(store, data):
    run = queued(store, data)
    remote = Jenkins()
    base = remote.__call__

    def changed(request):
        response = base(request)
        if request.url.path == "/queue/item/7/api/json":
            body = response.json()
            body["actions"][0]["parameters"][0]["value"] = "another-run"
            return httpx.Response(200, json=body)
        return response

    process_one(store, reader(), runner(changed))
    assert store.run(run["id"])["error"]["code"] == "build_identity_mismatch"


def test_jenkins_is_default_and_does_not_fall_back_to_codebuild():
    assert settings().build_provider == "jenkins"
    with pytest.raises(Problem, match="Jenkins"):
        build_target(
            settings().model_copy(update={"build_project": "old", "build_repository": REPOSITORY})
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://user:pass@ci.test",
        "https://ci.test/?secret=1",
        "https://ci.test/../other",
        "http://ci.test",
        "https://ci.test/%2fother",
    ],
)
def test_invalid_jenkins_url_is_rejected(url):
    with pytest.raises(Problem):
        build_target(config().model_copy(update={"jenkins_url": url}))


def test_unknown_stored_provider_is_rejected():
    from delta_platform.adapters.builds import BuildError

    with pytest.raises(BuildError):
        build_runner(config(), {"input_snapshot": {"build_target": {"provider": "unknown"}}})


def test_jenkins_gzip_json_is_decoded_once():
    import gzip

    def response(request):
        return httpx.Response(
            200, headers={"Content-Encoding": "gzip"}, content=gzip.compress(b'{"number":3}')
        )

    assert runner(response).get("/job/delta-platform-image/3/api/json") == {"number": 3}


def test_evicted_queue_is_resolved_from_job_history_without_resubmission(store, data):
    run = queued(store, data)
    remote = Jenkins()

    def handler(request):
        if request.url.path == "/queue/item/7/api/json":
            return httpx.Response(404)
        return remote(request)

    process_one(store, reader(), runner(handler))
    assert store.run(run["id"])["status"] == "succeeded" and remote.posts == 1


def test_timeout_stop_verifies_the_execution_and_does_not_follow_redirects(store, data):
    from delta_platform.adapters.builds import BuildError

    queued(store, data)
    claim = store.claim()
    remote = Jenkins()
    remote.parameters = runner(remote).parameters(claim)
    stops = []

    def handler(request):
        if request.url.path.endswith("/stop"):
            stops.append(request.url.path)
            return httpx.Response(302, headers={"Location": "https://untrusted.test"})
        response = remote(request)
        body = response.json()
        body["building"] = True
        return httpx.Response(200, json=body)

    runner(handler).stop_verified("jenkins:build:3", claim)
    assert stops == ["/job/delta-platform-image/3/stop"]
    remote.parameters["DELTA_COMMIT"] = "0" * 40
    with pytest.raises(BuildError):
        runner(handler).stop_verified("jenkins:build:3", claim)
    assert len(stops) == 1
