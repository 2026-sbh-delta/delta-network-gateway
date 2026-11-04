import copy
import json
from uuid import uuid4

import pytest

from dast_agent import cloud_run
from dast_agent.registry import load_catalog

CFG = {"project": "test-project", "region": "asia-northeast3", "service_prefix": "delta-sbx",
       "repository": "asia-northeast3-docker.pkg.dev/test-project/sandbox/app",
       "runtime_service_account": "runtime@test-project.iam.gserviceaccount.com",
       "scanner_service_account": "scanner@test-project.iam.gserviceaccount.com"}


class Response:
    def __init__(self, body):
        self.body = body
    def raise_for_status(self):
        pass
    def json(self):
        return self.body


class Client:
    def __init__(self, service):
        self.service = service
        self.tokens = []
    def get(self, url, **kwargs):
        if "/revisions/" in url:
            return Response({"containers": self.service["template"]["containers"]})
        return Response(self.service)
    def post(self, url, **kwargs):
        self.tokens.append(kwargs["json"])
        return Response({"token": "private-test-token"})


def fixture(monkeypatch):
    monkeypatch.setenv("DAST_GCP_TARGET", json.dumps(CFG))
    monkeypatch.setenv("DAST_PLATFORM_TARGET_ID", "local-fixture")
    run_id = str(uuid4())
    resource = f"projects/test-project/locations/asia-northeast3/services/delta-sbx-{uuid4().hex}"
    resource = resource.rsplit("-", 1)[0] + "-" + run_id.replace("-", "")
    binding = {"execution_cloud": "gcp", "runtime_resource": resource, "commit_sha": "a" * 40,
               "target_url": "https://owned.run.app", "image_digest": "sha256:" + "b" * 64}
    revision = resource + "/revisions/delta-sbx-" + run_id.replace("-", "") + "-00001-abc"
    service = {"name": resource, "uri": binding["target_url"],
               "labels": {"delta-run-id": run_id, "delta-commit": "a" * 40},
               "template": {"containers": [{"image": CFG["repository"] + "@" + binding["image_digest"]}],
                            "serviceAccount": CFG["runtime_service_account"]},
               "latestReadyRevision": revision, "latestCreatedRevision": revision,
               "terminalCondition": {"state": "CONDITION_SUCCEEDED"}}
    return run_id, binding, Client(service), load_catalog()


def test_only_verified_origin_receives_audience_bound_token(monkeypatch):
    run_id, binding, client, catalog = fixture(monkeypatch)
    target_id = cloud_run.register(catalog, run_id, binding, client=client)
    assert catalog.targets[target_id].origin == binding["target_url"]
    assert cloud_run.authorization("https://other.run.app") == {}
    assert cloud_run.authorization(binding["target_url"]) == {"X-Serverless-Authorization": "Bearer private-test-token"}
    cloud_run.authorization(binding["target_url"])
    assert client.tokens == [{"audience": binding["target_url"], "includeEmail": True}]
    cloud_run.forget(catalog, run_id, binding)
    assert cloud_run.authorization(binding["target_url"]) == {} and target_id not in catalog.targets


@pytest.mark.parametrize("field,value", [
    ("name", "foreign"), ("uri", "http://169.254.169.254"), ("invokerIamDisabled", True),
    ("latestReadyRevision", "projects/foreign/revision"), ("reconciling", True)])
def test_changed_remote_identity_cannot_register(monkeypatch, field, value):
    run_id, binding, client, catalog = fixture(monkeypatch)
    client.service[field] = value
    original = copy.deepcopy(catalog.targets)
    with pytest.raises(ValueError):
        cloud_run.register(catalog, run_id, binding, client=client)
    assert catalog.targets == original and client.tokens == []


def test_request_cannot_borrow_another_run(monkeypatch):
    run_id, binding, client, catalog = fixture(monkeypatch)
    with pytest.raises(ValueError):
        cloud_run.register(catalog, str(uuid4()), binding, client=client)
