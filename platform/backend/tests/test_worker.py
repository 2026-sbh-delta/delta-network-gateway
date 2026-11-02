import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import sqlalchemy as sa
from github_fixture import SHA, handler

from delta_platform.adapters.github import GitHubReader
from delta_platform.core.errors import LeaseLost, Problem
from delta_platform.core.schema import runs
from delta_platform.worker.main import process_one


def queue(store, data, key="run"):
    service, _ = store.register(data, "registration")
    return store.enqueue(service["id"], key)[0]


def expire(engine, run_id):
    with engine.begin() as c:
        c.execute(
            runs.update()
            .where(runs.c.id == run_id)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )


def reader():
    return GitHubReader(httpx.Client(transport=httpx.MockTransport(handler)))


def test_worker_full_result_and_events_persist(store, data):
    run = queue(store, data)
    assert process_one(store, reader())
    result = store.run(run["id"])
    assert result["status"] == "succeeded" and result["resolved_commit_sha"] == SHA
    assert "image_digest" not in result["result"]
    events = store.run_events(run["id"], 0, 200)["items"]
    assert events[-1]["event_type"] == "succeeded"
    assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
    replay, new = store.enqueue(run["service_id"], "run")
    assert not new and replay["id"] == run["id"] and replay["status"] == "succeeded"


def test_concurrent_enqueue_and_claim(store, data):
    service, _ = store.register(data, "service")
    with ThreadPoolExecutor(max_workers=6) as pool:
        same = list(pool.map(lambda _: store.enqueue(service["id"], "same"), range(6)))
    assert len({r[0]["id"] for r in same}) == 1 and sum(r[1] for r in same) == 1
    with pytest.raises(Problem) as exc:
        store.enqueue(service["id"], "other")
    assert exc.value.code == "source_check_in_progress"
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: store.claim(), range(2)))
    assert sum(c is not None for c in claims) == 1


def test_expired_owner_cannot_write_or_heartbeat(store, engine, data):
    run = queue(store, data)
    old = store.claim()
    store.progress(run["id"], old["lease_token"], "revision", "pin", SHA)
    expire(engine, run["id"])
    with pytest.raises(LeaseLost):
        store.heartbeat(run["id"], old["lease_token"])
    new = store.claim()
    assert new["resolved_commit_sha"] == SHA and new["lease_token"] != old["lease_token"]
    with pytest.raises(LeaseLost):
        store.finish(run["id"], old["lease_token"], result={})
    with pytest.raises(LeaseLost):
        store.progress(run["id"], old["lease_token"], "dockerfile", "late")
    store.finish(run["id"], new["lease_token"], result={"commit": SHA})
    assert store.run(run["id"])["status"] == "succeeded"


def test_retry_cap_recovery_and_atomic_completion(store, engine, data):
    run = queue(store, data)
    for _ in range(3):
        claim = store.claim()
        assert claim
        expire(engine, run["id"])
    assert store.claim() is None
    assert store.run(run["id"])["error"]["code"] == "attempts_exhausted"
    assert store.run_events(run["id"], 0, 100)["items"][-1]["event_type"] == "failed"


def test_transient_failure_retries_with_pinned_sha(store, engine, data):
    run = queue(store, data)
    paths = []
    broken = True

    def transport(request):
        paths.append(request.url.path)
        if "/git/trees/" in request.url.path and broken:
            return httpx.Response(503)
        return handler(request)

    r = GitHubReader(httpx.Client(transport=httpx.MockTransport(transport)))
    process_one(store, r)
    assert (
        store.run(run["id"])["status"] == "queued"
        and store.run(run["id"])["resolved_commit_sha"] == SHA
    )
    assert store.claim() is None
    with engine.begin() as c:
        c.execute(
            runs.update()
            .where(runs.c.id == run["id"])
            .values(available_at=sa.func.clock_timestamp())
        )
    broken = False
    process_one(store, r)
    assert store.run(run["id"])["status"] == "succeeded"
    assert f"/repos/example/orders/commits/{SHA}" in paths


def test_real_worker_process_kill_and_recovery(store, engine, data):
    run = queue(store, data)
    code = """
import sys,time
sys.path.insert(0,'tests')
from support import settings
from delta_platform.core.database import engine_for
from delta_platform.core.store import Store
from delta_platform.worker.main import process_one
class BlockingReader:
    def inspect(self, snapshot, sha, progress):
        progress('revision','커밋 고정','a'*40)
        time.sleep(300)
process_one(Store(engine_for(settings())), BlockingReader())
"""
    child = subprocess.Popen([sys.executable, "-c", code])
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if store.run(run["id"])["resolved_commit_sha"] == SHA:
                break
            time.sleep(0.05)
        assert store.run(run["id"])["resolved_commit_sha"] == SHA
        child.kill()
        child.wait(timeout=5)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
    expire(engine, run["id"])
    assert process_one(store, reader())
    result = store.run(run["id"])
    assert result["status"] == "succeeded" and result["attempt"] == 2


def test_attempt_deadline_limits_heartbeat(store, engine, data):
    run = queue(store, data)
    claim = store.claim()
    with engine.begin() as c:
        c.execute(
            runs.update()
            .where(runs.c.id == run["id"])
            .values(started_at=datetime.now(UTC) - timedelta(seconds=121))
        )
    with pytest.raises(LeaseLost):
        store.heartbeat(run["id"], claim["lease_token"])


def test_distinct_services_can_run_and_event_pagination(store, data):
    first = queue(store, data)
    second_service, _ = store.register(
        data.model_copy(update={"name": "another-service"}), "other-registration"
    )
    second, _ = store.enqueue(second_service["id"], "other-run")
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: store.claim(), range(2)))
    assert {c["id"] for c in claims} == {first["id"], second["id"]}
    first_page = store.run_events(first["id"], 0, 1)
    second_page = store.run_events(first["id"], first_page["next_sequence"], 1)
    assert first_page["has_more"] and first_page["items"][0]["sequence"] == 1
    assert second_page["items"][0]["sequence"] == 2


def test_failed_terminal_event_rolls_back_with_result(store, data, monkeypatch):
    run = queue(store, data)
    claim = store.claim()
    import delta_platform.core.store as module

    def broken_event(*args, **kwargs):
        raise RuntimeError("injected event failure")

    monkeypatch.setattr(module, "append_event", broken_event)
    with pytest.raises(RuntimeError):
        store.finish(run["id"], claim["lease_token"], error={"code": "test", "message": "failed"})
    assert store.run(run["id"])["status"] == "running"
