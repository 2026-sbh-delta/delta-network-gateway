from datetime import timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from test_observability import NOW, environment

from delta_platform.adapters.observability import DashboardReader
from delta_platform.adapters.onprem_observability import OnpremInfrastructure
from delta_platform.core.environments import EnvironmentInput

HOST = "onprem-" + "a" * 32


def onprem():
    env = environment()
    env.update(cloud="onprem", region="onprem-lab")
    env["binding"] = {"instances": [{"id": HOST, "name": "lab-vm"}]}
    return env


def test_onprem_binding_is_registered_identity_not_arbitrary_endpoint():
    env = onprem()
    body = {**{k: env[k] for k in ("name", "cloud", "region", "version")}, **env["binding"]}
    assert EnvironmentInput(**body).instances[0].id == HOST
    for change in (
        {"account_id": "123456789012"},
        {"instances": [{"id": "169.254.169.254", "name": "metadata"}]},
        {"instances": [{"id": "i-0123456789abcdef0", "name": "aws"}]},
        {"instances": [{"id": HOST, "name": "vm", "zone": "asia-northeast3-a"}]},
        {"endpoint": "https://arbitrary.invalid"},
    ):
        with pytest.raises(ValidationError):
            EnvironmentInput(**{**body, **change})


def provider(*, observed=NOW, missing_memory=False, error_disk=False):
    seen = []

    def query(expression, *, start=None, end=None):
        seen.append(expression)
        assert end == NOW
        if missing_memory and "system.memory.utilization" in expression:
            return []
        if error_disk and "system.filesystem.utilization" in expression:
            raise RuntimeError("private token")
        value = str(observed.timestamp()) if "timestamp(" in expression else "25"
        return [
            {"values": [[NOW.timestamp(), value]]} if start else {"value": [NOW.timestamp(), value]}
        ]

    return SimpleNamespace(query=query), seen


def test_cpu_memory_disk_queries_bind_host_service_environment_and_units():
    env = onprem()
    telemetry, seen = provider()
    result = OnpremInfrastructure(telemetry).read(env, NOW - timedelta(minutes=15), NOW)
    row = result["instances"][0]
    assert result["status"] == "ok" and row["state"] == "reporting"
    assert all(row[k]["value"] == 25 for k in ("cpu_percent", "memory_percent", "disk_percent"))
    assert all(HOST in q and str(env["id"]) in q and str(env["service_id"]) in q for q in seen)
    assert any("(1 - avg(" in q and '"state"="idle"' in q for q in seen)
    assert all('"state"' not in q for q in seen if "system.filesystem.utilization" in q)


def test_missing_and_old_samples_never_become_zero_or_running():
    telemetry, _ = provider(observed=NOW - timedelta(minutes=6), missing_memory=True)
    result = OnpremInfrastructure(telemetry).read(onprem(), NOW - timedelta(minutes=15), NOW)
    row = result["instances"][0]
    assert result["status"] == row["state"] == "stale"
    assert row["cpu_percent"]["status"] == "stale"
    assert row["memory_percent"]["status"] == "no_data"
    assert row["memory_percent"]["value"] is None
    assert row["memory_percent"]["collected_at"] is None


def test_failure_is_partial_and_redacted():
    telemetry, _ = provider(error_disk=True)
    result = OnpremInfrastructure(telemetry).read(onprem(), NOW - timedelta(minutes=15), NOW)
    row = result["instances"][0]
    assert result["status"] == row["disk_percent"]["status"] == "error"
    assert row["cpu_percent"]["status"] == "ok"
    assert "private token" not in str(result)


def test_dashboard_dispatches_onprem_to_common_store_without_cloud_vm_queries():
    telemetry, _ = provider()
    telemetry.metrics = lambda *args: {}
    telemetry.logs = lambda *args: {"items": []}
    telemetry.trace = lambda *args: {"spans": []}
    result = DashboardReader("ap-northeast-2", telemetry=telemetry, clock=lambda: NOW).read(
        onprem(), 15
    )
    assert result["infra"]["instances"][0]["id"] == HOST


def test_no_registered_hosts_does_not_query_store():
    env = onprem()
    env["binding"]["instances"] = []
    telemetry, seen = provider()
    assert OnpremInfrastructure(telemetry).read(env, NOW, NOW)["status"] == "no_data"
    assert not seen
