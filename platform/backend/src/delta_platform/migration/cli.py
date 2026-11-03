"""Operator entry point: secret connections come only from the process environment."""

import argparse
import json
import os
from pathlib import Path
from uuid import UUID

import psycopg

from delta_platform.core.store import digest
from delta_platform.migration.postgres import Journal, MigrationError, PostgresMove


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
    args = parser.parse_args()
    if args.operation == "plan":
        if not args.source_environment_id or not args.target_environment_id or not args.writer_role:
            parser.error("plan에는 출발·대상 환경과 전용 쓰기 계정이 필요합니다.")
    elif not all((args.journal, args.workflow_id, args.run_id)):
        parser.error("계획의 workflow/run ID와 영속 journal 경로가 필요합니다.")
    try:
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
            plan = json.loads(args.plan.read_text())
            with Journal(
                args.journal, plan, workflow_id=args.workflow_id, run_id=args.run_id
            ) as journal:
                if args.operation == "start":
                    result = driver.start(journal, args.approved_hash)
                elif args.operation == "promote":
                    result = driver.promote(journal, args.approved_hash)
                elif args.operation == "wait":
                    result = driver.wait_ready(journal)
                elif args.operation == "abort":
                    driver.abort_before_promotion(journal)
                    result = {"phase": "aborted"}
                else:
                    result = getattr(driver, args.operation)(journal)
                print(json.dumps(result))
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


if __name__ == "__main__":
    main()
