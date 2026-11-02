import logging
import signal
import threading
import time

from delta_platform.adapters.github import GitHubReader, SourceError
from delta_platform.config import Settings
from delta_platform.core.auth import Auth
from delta_platform.core.database import engine_for, ready
from delta_platform.core.errors import LeaseLost
from delta_platform.core.store import Store

logger = logging.getLogger("delta_platform.worker")


def process_one(store, reader):
    claim = store.claim()
    if claim is None:
        return False
    run_id, token = claim["id"], claim["lease_token"]
    stop = threading.Event()
    lost = threading.Event()

    def heartbeat():
        while not stop.wait(15):
            try:
                store.heartbeat(run_id, token)
            except Exception:
                lost.set()
                return

    def progress(step, message, sha=None):
        if lost.is_set():
            raise LeaseLost()
        store.progress(run_id, token, step, message, sha)

    beat = threading.Thread(target=heartbeat, daemon=True)
    beat.start()
    try:
        result = reader.inspect(claim["input_snapshot"], claim["resolved_commit_sha"], progress)
        if lost.is_set():
            raise LeaseLost()
        store.finish(run_id, token, result=result)
    except SourceError as exc:
        try:
            store.finish(run_id, token, error=exc.payload, retry=exc.retry)
        except LeaseLost:
            logger.info("lease_lost run_id=%s", run_id)
    except LeaseLost:
        logger.info("lease_lost run_id=%s", run_id)
    except Exception:
        # Leave the lease for recovery; do not leak upstream bodies or credentials.
        logger.error("worker_attempt_interrupted run_id=%s", run_id)
    finally:
        stop.set()
        beat.join(timeout=5)
    return True


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    settings = Settings()
    engine = engine_for(settings)
    if not ready(engine):
        raise SystemExit("DB migration을 먼저 실행하세요.")
    store, auth, reader = Store(engine), Auth(engine, settings), GitHubReader()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    next_cleanup = 0
    try:
        while not stop.is_set():
            try:
                if time.monotonic() >= next_cleanup:
                    auth.cleanup()
                    next_cleanup = time.monotonic() + 60
                worked = process_one(store, reader)
            except Exception:
                logger.error("worker_store_unavailable")
                worked = False
            if not worked:
                stop.wait(1)
    finally:
        reader.close()
        engine.dispose()
