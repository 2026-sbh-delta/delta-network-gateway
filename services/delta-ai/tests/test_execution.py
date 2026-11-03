from copy import deepcopy
from uuid import uuid4

from fastapi.testclient import TestClient

from delta_ai.api import create_app
from delta_ai.execution import payload_hash
from delta_ai.store import RuleStore

AUTH = {"Authorization": "Bearer bound-test"}


def bound(raw):
    raw = deepcopy(raw)
    raw.pop("assessment_id", None)
    raw["service"] = {"service_id": str(uuid4()), "repo": "https://github.com/example/app", "commit": "a" * 40}
    context = {
        "project_id": str(uuid4()), "service_id": raw["service"]["service_id"],
        "workflow_id": str(uuid4()), "run_id": str(uuid4()), "environment_id": str(uuid4()),
        "source_environment_id": str(uuid4()), "source_provider": raw["source_env"]["provider"],
        "target_provider": raw["target_env"]["provider"], "repository_url": raw["service"]["repo"],
        "commit_sha": "a" * 40, "image_digest": "sha256:" + "b" * 64,
        "plan_hash": "c" * 64, "payload_hash": payload_hash(raw),
    }
    return {**raw, "assessment_id": context["run_id"], "execution_context": context}


def test_bound_result_survives_restart_and_rejects_changed_evidence(tmp_path, parity_ok):
    store = RuleStore(tmp_path / "rules.json")
    raw = bound(parity_ok)
    client = TestClient(create_app(llm=None, store=store, token="bound-test"))
    response = client.post("/v1/assessments", json=raw, headers=AUTH)
    assert response.status_code == 200
    assert response.json()["execution_context"] == raw["execution_context"]
    restarted = TestClient(create_app(llm=None, store=store, token="bound-test"))
    assert restarted.post("/v1/assessments", json=raw, headers=AUTH).json() == response.json()
    changed = deepcopy(raw)
    changed["target_env"]["waf"]["enabled"] = False
    assert restarted.post("/v1/assessments", json=changed, headers=AUTH).status_code == 409
    changed["execution_context"]["payload_hash"] = payload_hash({k: v for k, v in changed.items() if k not in ("assessment_id", "execution_context")})
    assert restarted.post("/v1/assessments", json=changed, headers=AUTH).status_code == 409
    # A new execution may assess changed evidence.
    changed["assessment_id"] = changed["execution_context"]["run_id"] = str(uuid4())
    assert restarted.post("/v1/assessments", json=changed, headers=AUTH).status_code == 200


def test_bound_contract_rejects_cross_service_commit_provider(tmp_path, parity_ok):
    client = TestClient(create_app(llm=None, store=RuleStore(tmp_path / "rules.json"), token="bound-test"))
    for field, value in (("service_id", str(uuid4())), ("commit_sha", "f" * 40), ("target_provider", "onprem")):
        raw = bound(parity_ok)
        raw["execution_context"][field] = value
        assert client.post("/v1/assessments", json=raw, headers=AUTH).status_code == 422
