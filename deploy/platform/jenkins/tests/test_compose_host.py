"""common-host 동작 계약. 모듈 부재 실패는 RED/coverage가 아니다. skip 없음."""

import copy
import fcntl
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys

import pytest

from .delivery_support import canonical_hash
from .fixtures.compose_host_support import HostRuntime, load_host, wait_file


@pytest.fixture
def host_module():
    return load_host()


@pytest.fixture
def host_request(release_request, tmp_path):
    request = copy.deepcopy(release_request)
    target = request["target"]
    target.update(install_dir=str(tmp_path / "app"), db_host="127.0.0.1", db_port=5432,
                  db_user="fixture-app-user", db_secret_file=str(tmp_path / "backend-secret"),
                  log_group="/delta/workload/fixture-app", log_prefix="fixture-app",
                  sql_proxy_image="registry.invalid/fixture/proxy@sha256:" + "9" * 64,
                  sql_connection_name="fixture-project:fixture-region:fixture-db")
    Path(target["db_secret_file"]).write_text("synthetic-fixture-password-only")
    request["config_sha256"] = canonical_hash(target)
    return request


@pytest.fixture
def runtime(host_request, tmp_path):
    return HostRuntime(host_request, tmp_path / "runtime")


def deploy(module, request, state, runtime):
    return module.deploy(request, state, runner=runtime.runner, probe=runtime.probe)


def old_request(request):
    old = copy.deepcopy(request)
    old["deployment_id"] = "deployment-previous"
    old["commit_sha"] = old["bundle"]["commit"] = "d" * 40
    for service, character in (("frontend", "7"), ("backend", "8")):
        digest = "sha256:" + character * 64
        old["bundle"]["image_digests"][service] = digest
        old["bundle"]["images"][service] = "registry.invalid/app/" + service + "@" + digest
    old["bundle"]["image_ref"] = old["bundle"]["images"]["frontend"]
    old["bundle"]["image_digest"] = old["bundle"]["image_digests"]["frontend"]
    old["bundle"]["bundle_sha256"] = canonical_hash({key: old["bundle"][key]
        for key in ("schema", "commit", "platform", "image_digests")})
    return old


def seed_previous(module, request, state, runtime):
    old = old_request(request)
    assert deploy(module, old, state, runtime)["state"] == "ready"
    runtime.events.clear()
    runtime.calls.clear()
    return copy.deepcopy(runtime.active)


def assert_sqlite_record(state, deployment_id):
    databases = []
    for path in state.rglob("*"):
        if path.is_file():
            with path.open("rb") as stream:
                if stream.read(16) == b"SQLite format 3\x00":
                    databases.append(path)
    assert databases, "실제 SQLite 상태 기록이 없다"
    found = False
    for path in databases:
        with sqlite3.connect(path) as db:
            assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            for (table,) in tables:
                rows = db.execute('SELECT * FROM "' + table.replace('"', '""') + '"').fetchall()
                found |= deployment_id in repr(rows)
    assert found, "deployment_id가 SQLite에 보존되지 않았다"


def test_ready_links_both_container_config_ids_to_manifest_digests(host_module, host_request, runtime, tmp_path):
    state = tmp_path / "state"
    result = deploy(host_module, host_request, state, runtime)
    assert result["state"] == "ready"
    assert result["image_digests"] == host_request["bundle"]["image_digests"]
    assert result["bundle_sha256"] == host_request["bundle"]["bundle_sha256"]
    assert result["target_id"] == host_request["target"]["target_id"]
    assert result["commit_sha"] == host_request["commit_sha"]
    for service in ("frontend", "backend"):
        assert ["container-inspect", service] in runtime.events
        assert ["image-inspect", runtime.config_id(service), service] in runtime.events
        assert runtime.config_id(service) != result["image_digests"][service]
    assert_sqlite_record(state, host_request["deployment_id"])
    assert load_host().query(host_request["deployment_id"], state) == result


def test_same_deployment_reuses_persisted_result_without_docker(host_module, host_request, runtime, tmp_path):
    state = tmp_path / "state"
    first = deploy(host_module, host_request, state, runtime)
    before = list(runtime.calls)
    assert deploy(load_host(), host_request, state, runtime) == first
    assert runtime.calls == before, "동일 deployment_id 재접수가 호스트를 다시 변경했다"


def test_same_id_cannot_replace_backend_bundle(host_module, host_request, runtime, tmp_path):
    state = tmp_path / "state"
    deploy(host_module, host_request, state, runtime)
    changed = copy.deepcopy(host_request)
    digest = "sha256:" + "e" * 64
    changed["bundle"]["images"]["backend"] = "registry.invalid/app/backend@" + digest
    changed["bundle"]["image_digests"]["backend"] = digest
    changed["bundle"]["bundle_sha256"] = canonical_hash({key: changed["bundle"][key]
        for key in ("schema", "commit", "platform", "image_digests")})
    before = list(runtime.calls)
    with pytest.raises(ValueError):
        deploy(host_module, changed, state, runtime)
    assert runtime.calls == before


def test_unknown_a_survives_reload_and_blocks_b_even_after_lock_release(host_module, host_request, runtime, tmp_path):
    state = tmp_path / "state"
    runtime.unknown_on_up = True
    assert deploy(host_module, host_request, state, runtime)["state"] == "unknown"
    assert_sqlite_record(state, host_request["deployment_id"])
    fresh = load_host()
    assert fresh.query(host_request["deployment_id"], state)["state"] == "unknown"
    before = list(runtime.calls)
    other = {**host_request, "deployment_id": "deployment-b"}
    blocked = deploy(fresh, other, state, runtime)
    assert blocked["state"] == "blocked" and blocked["blocked_by"] == host_request["deployment_id"]
    assert runtime.calls == before


def start_worker(request, tmp_path, command="run"):
    from .fixtures import compose_host_support
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request))
    return subprocess.Popen(
        [sys.executable, str(Path(compose_host_support.__file__).resolve()), command,
         "--request", str(path), "--state-dir", str(tmp_path / "state")],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def test_live_worker_holds_real_flock_and_blocks_different_process_deployment(host_module, host_request, runtime, tmp_path):
    worker = start_worker(host_request, tmp_path)
    try:
        wait_file(tmp_path / "runtime" / "entered.json")
        locks = list((tmp_path / "state").rglob("*.lock"))
        assert locks, "대상별 flock 파일이 없다"
        held = False
        for path in locks:
            with path.open("a") as stream:
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    held = True
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)
        assert held, "교체/검증이 진행 중인데 실제 OS 파일 잠금이 풀렸다"
        blocked = deploy(host_module, {**host_request, "deployment_id": "deployment-b"}, tmp_path / "state", runtime)
        assert blocked["state"] == "blocked" and blocked["blocked_by"] == host_request["deployment_id"]
        assert not runtime.calls
    finally:
        (tmp_path / "runtime" / "release").touch()
        stdout, stderr = worker.communicate(timeout=8)
    assert worker.returncode == 0, (stdout, stderr)


def test_backend_pull_failure_never_ups_and_preserves_original_stopped_state(host_module, host_request, runtime, tmp_path):
    runtime.fail_pull = "backend"
    result = deploy(host_module, host_request, tmp_path / "state", runtime)
    assert ["pull", "backend"] in runtime.events, "BE pull 실패 주입에 도달하지 않았다"
    assert result["state"] == "failed"
    assert not any(event[0] == "up" for event in runtime.events)
    assert runtime.active is None


def test_healthy_frontend_wrong_backend_runtime_digest_restores_both_images_and_config(host_module, host_request, runtime, tmp_path):
    state = tmp_path / "state"
    previous = seed_previous(host_module, host_request, state, runtime)
    runtime.wrong_digest = True
    result = deploy(host_module, host_request, state, runtime)
    assert result["state"] == "failed"
    ups = [event[1] for event in runtime.events if event[0] == "up"]
    assert len(ups) == 2 and runtime.active == previous and ups[-1] == previous
    assert set(ups[-1]["services"]) >= {"frontend", "backend"}
    for service in ("frontend", "backend"):
        assert ["container-inspect", service] in runtime.events
        assert sum(event[:1] == ["image-inspect"] and event[-1] == service for event in runtime.events) >= 2


@pytest.mark.parametrize("diagnostic", [
    {"server_version_num": 160008}, {"schema_ok": False, "columns": []},
    {"server_version_num": None},
], ids=["actual-pg16-not-config18", "missing-schema", "missing-observed-version"])
def test_observed_db_preflight_failure_stops_before_pull_or_up(host_module, host_request, runtime, tmp_path, diagnostic):
    runtime.db_change = diagnostic
    assert host_request["target"]["db_major"] == 18
    result = deploy(host_module, host_request, tmp_path / "state", runtime)
    assert result["state"] == "failed"
    assert not any(event[0] in ("pull", "up", "stop") for event in runtime.events)
    assert runtime.active is None


@pytest.mark.parametrize("diagnostic", [
    {"backend": {"storage": "memory"}},
    {"frontend": {"cloudProvider": "aws"}},
    {"frontend": {"version": "e" * 40}},
], ids=["healthy-memory-fallback", "wrong-runtime-cloud", "wrong-runtime-version"])
def test_healthy_http_with_wrong_app_diagnostic_rolls_back(host_module, host_request, runtime, tmp_path, diagnostic):
    state = tmp_path / "state"
    previous = seed_previous(host_module, host_request, state, runtime)
    runtime.health_change = diagnostic
    assert deploy(host_module, host_request, state, runtime)["state"] == "failed"
    assert runtime.saw_new and runtime.active == previous


def test_previous_bundle_restore_failure_is_persisted_as_rollback_failed(host_module, host_request, runtime, tmp_path):
    state = tmp_path / "state"
    seed_previous(host_module, host_request, state, runtime)
    runtime.wrong_digest = runtime.fail_rollback = True
    result = deploy(host_module, host_request, state, runtime)
    assert result["state"] == "rollback_failed"
    assert load_host().query(host_request["deployment_id"], state)["state"] == "rollback_failed"
    assert len([event for event in runtime.events if event[0] == "up"]) == 2


def test_first_release_health_failure_restores_original_stopped_state(host_module, host_request, runtime, tmp_path):
    runtime.health_change = {"backend": {"healthy": False}}
    assert deploy(host_module, host_request, tmp_path / "state", runtime)["state"] == "failed"
    assert any(event[0] == "up" for event in runtime.events)
    assert runtime.active is None and ["stop"] in runtime.events


@pytest.mark.parametrize("cloud,driver", [("gcp", "gcplogs"), ("aws", "awslogs")])
def test_common_render_digest_only_private_backend_and_enterprise_nonblocking_logs(host_module, host_request, cloud, driver):
    target = {**host_request["target"], "cloud": cloud, "log_driver": driver, "region": "fixture-region"}
    doc = host_module.render_compose(target, host_request["bundle"])
    assert isinstance(doc, dict), "render_compose는 Compose document dict를 반환한다"
    services = doc["services"]
    assert {"frontend", "backend"} <= set(services)
    for name, service in services.items():
        assert "build" not in service, name
    for name in ("frontend", "backend"):
        service = services[name]
        assert service["image"] == host_request["bundle"]["images"][name]
        env = service["environment"]
        assert env["APP_ENV"] == "production" and env["CLOUD_PROVIDER"] == cloud
        assert env["APP_VERSION"] == host_request["commit_sha"]
        assert service["logging"]["driver"] == driver
        assert service["logging"]["options"]["mode"] == "non-blocking"
    assert not services["backend"].get("ports")
    assert services["frontend"]["environment"]["API_HOST"] == "backend"
    assert services["frontend"].get("ports")
    assert "synthetic-fixture-password-only" not in json.dumps(doc)
    secret_path = host_request["target"]["db_secret_file"]
    assert secret_path in json.dumps(services["backend"]), "BE 비밀 파일 mount가 없다"
    assert secret_path not in json.dumps(services["frontend"]), "FE에 BE 비밀 파일을 제공했다"
    assert "/delta-cp/" not in json.dumps([services[name]["logging"] for name in ("frontend", "backend")])
    if cloud == "aws":
        for name in ("frontend", "backend"):
            assert services[name]["logging"]["options"]["awslogs-group"].startswith("/delta/workload/")
    else:
        for name in ("frontend", "backend"):
            assert services[name]["logging"]["options"]["gcp-project"] == target["log_project"]


def test_cli_start_returns_while_detached_worker_owns_deployment_and_query_survives_launcher_exit(host_module, host_request, tmp_path):
    launcher = start_worker(host_request, tmp_path, "start")
    child = None
    try:
        stdout, stderr = launcher.communicate(timeout=4)
        assert launcher.returncode == 0, (stdout, stderr)
        child = wait_file(tmp_path / "runtime" / "entered.json")
        assert child["pid"] != launcher.pid and child["sid"] == child["pid"]
        assert host_module.query(host_request["deployment_id"], tmp_path / "state")["state"] == "running"
        (tmp_path / "runtime" / "release").touch()
        import time
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = host_module.query(host_request["deployment_id"], tmp_path / "state")
            if result["state"] == "ready":
                break
            time.sleep(0.02)
        assert result["state"] == "ready", result
        assert_sqlite_record(tmp_path / "state", host_request["deployment_id"])
        from .fixtures import compose_host_support
        queried = subprocess.run(
            [sys.executable, str(Path(compose_host_support.__file__).resolve()), "query",
             "--deployment-id", host_request["deployment_id"], "--state-dir", str(tmp_path / "state")],
            capture_output=True, text=True, timeout=4,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        assert queried.returncode == 0, queried.stderr
        assert json.loads(queried.stdout)["state"] == "ready"
    finally:
        (tmp_path / "runtime").mkdir(exist_ok=True)
        (tmp_path / "runtime" / "release").touch(exist_ok=True)
        if launcher.poll() is None:
            launcher.terminate()
            launcher.communicate(timeout=4)
        if child:
            try:
                os.kill(child["pid"], signal.SIGTERM)
            except ProcessLookupError:
                pass
