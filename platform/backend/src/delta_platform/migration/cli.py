"""Operator entry point: secret connections come only from the process environment."""

import argparse
import json
import os
from pathlib import Path
from uuid import UUID

import psycopg
from sqlalchemy.exc import SQLAlchemyError

from delta_platform.core.errors import Problem
from delta_platform.core.store import digest
from delta_platform.migration.postgres import Journal, MigrationError, PostgresMove


def execute(driver, journal, operation, approved_hash, *, tracker=None, context=None):
    def publish(result=None, blocked=False):
        if tracker:
            tracker.record(
                context,
                {
                    "phase": journal.state["phase"],
                    "validated": journal.state.get("validated", False),
                    "lag_bytes": (result or {}).get("lag_bytes"),
                },
                blocked=blocked,
            )

    # Verify approval and ownership again immediately before a DB operation.
    publish()
    try:
        if operation == "start":
            result = driver.start(journal, approved_hash)
        elif operation == "promote":
            result = driver.promote(journal, approved_hash)
        elif operation == "wait":
            result = driver.wait_ready(journal)
        elif operation == "abort":
            driver.abort_before_promotion(journal)
            result = {"phase": "aborted"}
        else:
            result = getattr(driver, operation)(journal)
    except Exception:
        try:
            publish(blocked=True)
        except Exception:
            # Durable local journal remains authoritative if the platform is unavailable.
            pass
        raise
    publish(result)
    return result


def main():
    parser = argparse.ArgumentParser(description="전용 PostgreSQL DB 복사·동기화·전환")
    parser.add_argument(
        "operation", choices=["plan", "start", "status", "wait", "promote", "abort", "cleanup"]
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--workflow-id", type=UUID)
    parser.add_argument("--run-id", type=UUID)
    parser.add_argument("--source-environment-id", type=UUID)
    parser.add_argument("--target-environment-id", type=UUID)
    parser.add_argument("--writer-role", action="append", default=[])
    parser.add_argument("--approved-hash")
    parser.add_argument("--local-testing", action="store_true")
    parser.add_argument(
        "--platform", action="store_true", help="승인된 플랫폼 workflow와 진행 상태 연결"
    )
    args = parser.parse_args()
    if args.operation == "plan":
        if not args.source_environment_id or not args.target_environment_id or not args.writer_role:
            parser.error("plan에는 출발·대상 환경과 전용 쓰기 계정이 필요합니다.")
    elif not all((args.journal, args.workflow_id)) or (not args.platform and not args.run_id):
        parser.error("계획의 workflow/run ID와 영속 journal 경로가 필요합니다.")
    if args.platform and args.operation == "plan":
        parser.error("DB 계획 생성 뒤 platform-db-register로 workflow를 등록하세요.")
    engine, tracker, context, plan = None, None, None, None
    try:
        if args.operation != "plan":
            if args.plan.stat().st_size > 1_000_000:
                raise ValueError("Plan too large")
            plan = json.loads(args.plan.read_text())
        if args.platform:
            from delta_platform.config import Settings
            from delta_platform.core.database import engine_for
            from delta_platform.migration.tracking import MigrationTrackingStore

            engine = engine_for(Settings())
            tracker = MigrationTrackingStore(engine)
            context = tracker.prepare(args.workflow_id, plan)
            if args.run_id and str(args.run_id) != context["run_id"]:
                raise MigrationError("플랫폼이 발급한 실행 ID와 다릅니다.")
            args.run_id = UUID(context["run_id"])
        with PostgresMove(
            os.environ["DELTA_MIGRATION_SOURCE_DSN"],
            os.environ["DELTA_MIGRATION_TARGET_DSN"],
            os.environ["DELTA_MIGRATION_REPLICATION_DSN"],
            local_testing=args.local_testing,
        ) as driver:
            if args.operation == "plan":
                plan = driver.plan(
                    source_environment_id=args.source_environment_id,
                    target_environment_id=args.target_environment_id,
                    writer_roles=args.writer_role,
                )
                args.plan.parent.mkdir(parents=True, exist_ok=True)
                descriptor = os.open(args.plan, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w") as stream:
                    json.dump(plan, stream, indent=2)
                print(
                    json.dumps({"plan_hash": digest(plan), "tables": len(plan["schema"]["tables"])})
                )
                return
            with Journal(
                args.journal, plan, workflow_id=args.workflow_id, run_id=args.run_id
            ) as journal:
                result = execute(
                    driver,
                    journal,
                    args.operation,
                    args.approved_hash,
                    tracker=tracker,
                    context=context,
                )
                if context:
                    result = {**result, "run_id": context["run_id"]}
                print(json.dumps(result))
    except Problem as exc:
        raise SystemExit(exc.message) from None
    except SQLAlchemyError:
        raise SystemExit(
            "플랫폼 DB에 연결·기록하지 못했습니다. journal을 보존하고 같은 실행을 재개하세요."
        ) from None
    except psycopg.Error as exc:
        # Server error text can contain a connection string from CREATE SUBSCRIPTION.
        raise SystemExit(
            f"DB 작업 실패 (SQLSTATE {exc.sqlstate or 'connection'}). 비공개 DB 운영 로그를 확인하세요."
        ) from None
    except (MigrationError, KeyError, ValueError, OSError) as exc:
        # Only our fixed-domain errors are printed; config values are never echoed.
        raise SystemExit(
            str(exc) if isinstance(exc, MigrationError) else "설정·계획·진행 기록을 확인하세요."
        ) from None
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    main()
