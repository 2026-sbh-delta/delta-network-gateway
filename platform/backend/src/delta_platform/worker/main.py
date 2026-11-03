import logging
import signal
import threading
import time

from botocore.exceptions import BotoCoreError, ClientError

from delta_platform.adapters.builds import BuildError, CodeBuildRunner
from delta_platform.adapters.github import GitHubReader, SourceError
from delta_platform.adapters.sandbox import SandboxError, SandboxRunner
from delta_platform.config import Settings
from delta_platform.core.auth import Auth
from delta_platform.core.database import engine_for, ready
from delta_platform.core.errors import LeaseLost
from delta_platform.core.store import Store

logger = logging.getLogger("delta_platform.worker")


def process_one(store, reader, builder=None, sandbox=None):
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
        if claim["kind"] == "image_build":
            builder = builder or CodeBuildRunner(Settings())
            result = builder.execute(claim, store, progress)
        elif claim["kind"] == "sandbox_check":
            sandbox = sandbox or SandboxRunner(Settings())
            result = sandbox.execute(claim, store, progress)
        else:
            result = reader.inspect(claim["input_snapshot"], claim["resolved_commit_sha"], progress)
        if lost.is_set():
            raise LeaseLost()
        store.finish(run_id, token, result=result)
    except (BuildError, SandboxError) as exc:
        try:
            store.finish(run_id, token, error=exc.payload)
        except LeaseLost:
            logger.info("lease_lost run_id=%s", run_id)
    except (BotoCoreError, ClientError) as exc:
        if isinstance(exc, ClientError):
            aws_code = exc.response.get("Error", {}).get("Code", "Unknown")
        else:
            aws_code = type(exc).__name__
        # Return only a bounded provider error code, not upstream bodies/credentials.
        denied = aws_code in {
            "AccessDeniedException",
            "AccessDenied",
            "UnrecognizedClientException",
            "NoCredentialsError",
        }
        try:
            store.finish(
                run_id,
                token,
                error={
                    "code": "build_provider_error",
                    "message": "빌드 실행기 연결에 실패했습니다. AWS 권한·연결 설정을 확인하세요.",
                    "provider_code": aws_code[:80],
                },
                retry=not denied,
            )
        except LeaseLost:
            logger.info("lease_lost run_id=%s", run_id)
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
