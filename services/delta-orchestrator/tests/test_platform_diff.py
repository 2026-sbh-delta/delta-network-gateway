import io
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from delta_ai.store import RuleStore
from delta_diff.api import create_app as diff_app
from delta_diff.sources import StateAllowlist
from delta_orchestrator.platform_diff import compare
from fastapi.testclient import TestClient


def request():
    context = {
        k: str(uuid4())
        for k in (
            "project_id",
            "service_id",
            "workflow_id",
            "run_id",
            "environment_id",
            "source_environment_id",
        )
    }
    context.update(
        plan_hash="a" * 64,
        payload_hash="b" * 64,
        source_provider="onprem",
        target_provider="gcp",
        repository_url="https://github.com/2026-sbh-delta/delta-sample-app",
    )
    manifest = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "app", "namespace": "delta-apps"},
        "spec": {
            "replicas": 1,
            "template": {"spec": {"containers": [{"name": "app", "image": "app:v1"}]}},
        },
    }
    return {
        "context": context,
        "target_region": "asia-northeast3",
        "source_manifests": json.dumps(manifest),
    }


def configured(tmp_path, mutate=None):
    api = TestClient(
        diff_app(
            reader=None,
            allowlist=StateAllowlist((("approved-bucket", "state.tfstate"),)),
            llm=None,
            store=RuleStore(tmp_path / "rules.json"),
            token="t" * 32,
        )
    )

    class Opener:
        def open(self, message, timeout):
            body = json.loads(message.data)
            assert "accepted_gaps" not in body and "scc_tier" not in body
            result = api.post(
                "/v1/diffs", json=body, headers={"Authorization": "Bearer " + "t" * 32}
            )
            assert result.status_code == 200
            data = result.json()
            if mutate:
                mutate(data)
            return io.BytesIO(json.dumps(data).encode())

    return lambda: SimpleNamespace(
        _url="http://diff.cp.internal:8095", _token="t" * 32, _opener=Opener()
    )


def test_authenticated_relay_uses_existing_diff_engine_and_exact_snapshot(tmp_path):
    body = request()
    output = compare(
        body,
        client_factory=configured(tmp_path),
        input_factory=lambda: SimpleNamespace(service_id="delta-sample-app"),
    )
    assert output["context"] == body["context"]
    assert output["assessment"]["assessment_id"] == body["context"]["run_id"]
    assert output["source"]["kind"] == "kubernetes_yaml"
    assert any(
        r["resource"] == "workload" for r in output["assessment"]["mapping"]["rows"]
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data["source"].update(sha256="0" * 64),
        lambda data: data.update(diff_id="another-run"),
        lambda data: data["assessment"].update(assessment_id="another-run"),
        lambda data: data["assessment"].update(checks=[]),
    ],
)
def test_unbound_response_cannot_attach_to_plan(tmp_path, mutation):
    with pytest.raises(ValueError):
        compare(
            request(),
            client_factory=configured(tmp_path, mutation),
            input_factory=lambda: SimpleNamespace(service_id="delta-sample-app"),
        )


def test_other_repository_and_aws_runtime_cannot_reuse_pinned_state():
    body = request()
    body["context"]["repository_url"] = "https://github.com/other/customer"
    with pytest.raises(ValueError):
        compare(
            body, input_factory=lambda: SimpleNamespace(service_id="delta-sample-app")
        )
    body = request()
    body["context"].update(source_provider="aws", target_provider="gcp")
    body["source_instance_ids"] = ["i-0208621f60dfdb338"]
    with pytest.raises(ValueError):
        compare(
            body, input_factory=lambda: SimpleNamespace(service_id="delta-sample-app")
        )


def test_relay_keeps_public_console_authentication(monkeypatch):
    from delta_orchestrator.app import create_app

    monkeypatch.delenv("ORCH_STATE_DIR", raising=False)
    monkeypatch.setenv("ORCH_PUBLIC_ORIGIN", "https://console.example")
    monkeypatch.setenv("ORCH_ADMIN_PASSWORD", "a-long-password-for-tests")
    with TestClient(create_app(), base_url="https://console.example") as client:
        assert client.post("/api/platform-diff", json=request()).status_code == 401
        assert (
            client.post(
                "/api/platform-diff",
                json=request(),
                headers={"Origin": "https://evil.example"},
            ).status_code
            == 403
        )
