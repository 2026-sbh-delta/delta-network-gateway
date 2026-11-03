from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import ValidationError
from support import LOGIN_ID, PASSWORD, settings

from delta_platform.adapters.gcp_observability import GcpInfrastructure
from delta_platform.adapters.observability import (
    CloudWatchTelemetry,
    DashboardReader,
    metric,
    safe_text,
)
from delta_platform.api.app import create_app
from delta_platform.core.environments import EnvironmentInput, EnvironmentStore
from delta_platform.core.errors import Problem

NOW = datetime(2026, 11, 4, 3, 0, tzinfo=UTC)


def target():
    return EnvironmentInput(
        name="AWS 서울",
        cloud="aws",
        region="ap-northeast-2",
        account_id="123456789012",
        instances=[{"id": "i-0123456789abcdef0", "name": "customer-app"}],
    )


def environment():
    data = target().model_dump()
    return {
        "id": uuid4(),
        "service_id": uuid4(),
        "project_id": uuid4(),
        **{k: data.pop(k) for k in ("name", "cloud", "region", "version")},
        "binding": data,
        "updated_at": NOW,
    }


def test_environment_persists_updates_without_changing_identity(engine, store, data):
    service, _ = store.register(data, "one")
    envs = EnvironmentStore(engine)
    first = envs.bind(service["id"], target())
    assert envs.bind(service["id"], target().model_copy(update={"version": "sha256:123"})) == first
    assert envs.get(service["id"], first)["version"] == "sha256:123"
    assert "binding" not in envs.list(service["id"])["items"][0]
    with pytest.raises(Problem, match="새 환경"):
        envs.bind(service["id"], target().model_copy(update={"region": "ap-northeast-1"}))
    with pytest.raises(RuntimeError, match="자동 downgrade"):
        command.downgrade(Config("alembic.ini"), "0005")
    assert envs.get(service["id"], first)["id"] == first


def test_environment_rejects_cross_service_queries_and_unbounded_windows(engine, store, data):
    service, _ = store.register(data, "one")
    other, _ = store.register(data.model_copy(update={"name": "other"}), "two")
    env_id = EnvironmentStore(engine).bind(service["id"], target())
    seen = []
    reader = SimpleNamespace(
        read=lambda env, minutes: seen.append((env, minutes)) or {"bound": str(env["service_id"])}
    )
    with TestClient(
        create_app(settings(), engine, dashboard=reader), base_url="http://127.0.0.1:8088"
    ) as client:
        path = f"/api/v1/services/{service['id']}/observability?environment_id={env_id}"
        assert client.get(path).status_code == 401
        client.headers.update({"Origin": "http://127.0.0.1:8088", "X-Platform-Request": "1"})
        assert (
            client.post(
                "/api/v1/auth/login", json={"login_id": LOGIN_ID, "password": PASSWORD}
            ).status_code
            == 200
        )
        assert client.get(path + "&minutes=121").status_code == 422
        assert client.get(path.replace(str(service["id"]), str(other["id"]))).status_code == 404
        assert not seen
        assert client.get(path + "&minutes=120").json() == {"bound": str(service["id"])}
        assert len(seen) == 1 and seen[0][1] == 120
        # No public API can bind arbitrary provider resources.
        assert client.post(
            f"/api/v1/services/{service['id']}/environments", json={}
        ).status_code in (403, 405)


def test_operator_binding_rejects_urls_and_invalid_resources():
    with pytest.raises(ValidationError):
        EnvironmentInput(**{**target().model_dump(), "endpoint": "http://169.254.169.254"})
    with pytest.raises(ValidationError):
        EnvironmentInput(
            **{**target().model_dump(), "instances": [{"id": "999", "name": "gcp-vm"}]}
        )


def test_metric_missing_stale_and_nonfinite_are_not_zero():
    assert metric()["value"] is None and metric()["status"] == "no_data"
    assert metric(0, at=NOW.timestamp() - 301, now=NOW)["status"] == "stale"
    assert metric(0, at=NOW.timestamp(), now=NOW)["status"] == "ok"


def test_provider_failures_are_partial_and_cache_does_not_cross_environments():
    count = []
    provider = SimpleNamespace(
        metrics=lambda *args: {"requests": metric(8)},
        logs=lambda *args: (_ for _ in ()).throw(RuntimeError("private credentials")),
        trace=lambda *args: {"status": "no_data", "id": None, "spans": []},
        aws_infra=lambda *args: count.append(args) or {"status": "ok", "instances": []},
    )
    reader = DashboardReader("ap-northeast-2", telemetry=provider, clock=lambda: NOW)
    env = environment()
    first = reader.read(env, 15)
    assert first["metrics"]["requests"]["value"] == 8
    assert first["logs"]["status"] == "error"
    assert "private credentials" not in str(first)
    first["metrics"]["requests"]["value"] = 999
    assert reader.read(env, 15)["metrics"]["requests"]["value"] == 8
    reader.read({**env, "id": uuid4()}, 15)
    assert len(count) == 2


def test_promql_delta_histogram_and_identity_are_bound():
    from botocore.credentials import Credentials

    env = environment()
    queries = []

    def handle(request):
        query = parse_qs(request.content.decode())["query"][0]
        queries.append(query)
        assert str(env["service_id"]) in query and str(env["id"]) in query
        assert "authorization" in request.headers
        if "timestamp(" in query:
            value = str(NOW.timestamp())
        elif "histogram_quantile" in query:
            value = "230"
        elif "vector(0)" in query:
            value = "NaN"
        else:
            value = "1"
        item = {
            "metric": {},
            "values" if request.url.path.endswith("query_range") else "value": [
                [NOW.timestamp(), value]
            ]
            if request.url.path.endswith("query_range")
            else [NOW.timestamp(), value],
        }
        return httpx.Response(200, json={"status": "success", "data": {"result": [item]}})

    session = SimpleNamespace(get_credentials=lambda: Credentials("test", "test"))
    reader = CloudWatchTelemetry(
        "ap-northeast-2", session=session, transport=httpx.MockTransport(handle)
    )
    result = reader.metrics(env, NOW - timedelta(minutes=15), NOW)
    assert result["p95_ms"]["value"] == 230
    assert result["error_rate"]["status"] == "no_data" and result["error_rate"]["value"] is None
    assert any("histogram_quantile(0.95, sum(sum_over_time(" in q for q in queries)
    assert not any("rate(" in q for q in queries)


def test_logs_are_bounded_and_secret_fields_are_not_returned():
    events = [
        {
            "timestamp": int(NOW.timestamp() * 1000) + i,
            "message": '{"body":"password=secret-token failure","severity_text":"ERROR","attributes":{"secret":"do-not-return"}}',
        }
        for i in range(20)
    ]
    reader = CloudWatchTelemetry("ap-northeast-2", session=SimpleNamespace())
    reader.client = lambda _: SimpleNamespace(get_log_events=lambda **kwargs: {"events": events})
    result = reader.logs(environment(), NOW - timedelta(minutes=15), NOW)
    assert len(result["items"]) == 5
    assert "secret-token" not in str(result) and "do-not-return" not in str(result)
    assert "[redacted]" in result["items"][0]["message"]
    assert "abc" not in safe_text("Authorization: Bearer abc")


def test_gcp_cpu_units_missing_memory_and_instance_identity():
    env = environment()
    env.update(
        cloud="gcp",
        region="asia-northeast3",
        binding={
            "gcp_project": "term2-delta",
            "instances": [{"id": "123456", "name": "app", "zone": "asia-northeast3-a"}],
        },
    )
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.host == "compute.googleapis.com":
            return httpx.Response(200, json={"id": "123456", "status": "RUNNING"})
        expression = request.url.params["filter"]
        assert 'instance_id = "123456"' in expression
        if "memory/percent_used" in expression:
            return httpx.Response(200, json={})
        return httpx.Response(
            200,
            json={
                "timeSeries": [
                    {
                        "points": [
                            {
                                "interval": {"endTime": NOW.isoformat()},
                                "value": {"doubleValue": 0.25},
                            }
                        ]
                    }
                ]
            },
        )

    reader = GcpInfrastructure(
        credentials=SimpleNamespace(valid=True, token="test"), transport=httpx.MockTransport(handle)
    )
    vm = reader.read(env, NOW - timedelta(minutes=15), NOW)["instances"][0]
    assert vm["cpu_percent"]["value"] == 25
    assert vm["memory_percent"]["status"] == "no_data"
    env["binding"]["instances"][0]["id"] = "789"
    with pytest.raises(ValueError, match="registered"):
        reader.read(env, NOW - timedelta(minutes=15), NOW)
