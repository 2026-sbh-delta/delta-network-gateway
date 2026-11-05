import os
from uuid import uuid4

import psycopg
import pytest
from delta_platform.core.store import digest
from delta_platform.migration.postgres import Journal, MigrationError, PostgresMove
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

SOURCE = os.environ["MIGRATION_TEST_SOURCE"]
TARGET = os.environ["MIGRATION_TEST_TARGET"]
REPLICA = os.environ["MIGRATION_TEST_REPLICA"]


@pytest.fixture(autouse=True)
def databases():
    for dsn, host in ((TARGET, "target"), (SOURCE, "source")):
        parsed = conninfo_to_dict(dsn)
        assert parsed["dbname"] == "migration_test" and parsed["host"] == host
        with psycopg.connect(dsn, autocommit=True) as connection:
            for (name,) in connection.execute("SELECT subname FROM pg_subscription"):
                connection.execute(
                    sql.SQL("ALTER SUBSCRIPTION {} DISABLE").format(sql.Identifier(name))
                )
                connection.execute(sql.SQL("DROP SUBSCRIPTION {}").format(sql.Identifier(name)))
            for (name,) in connection.execute("SELECT pubname FROM pg_publication"):
                connection.execute(sql.SQL("DROP PUBLICATION {}").format(sql.Identifier(name)))
            for (name,) in connection.execute(
                "SELECT slot_name FROM pg_replication_slots WHERE NOT active"
            ):
                connection.execute("SELECT pg_drop_replication_slot(%s)", (name,))
            connection.execute("DROP SCHEMA IF EXISTS public CASCADE")
            connection.execute("DROP SCHEMA IF EXISTS delta_transfer CASCADE")
            connection.execute("CREATE SCHEMA public")
            for name in ("app", "replicator", "unexpected_writer"):
                connection.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(name)))
            connection.execute("CREATE ROLE app LOGIN PASSWORD 'app-test-only'")
            connection.execute(
                "CREATE ROLE replicator LOGIN REPLICATION PASSWORD 'replication-test-only'"
            )
            connection.execute(
                "CREATE TABLE public.notes (id bigserial PRIMARY KEY, text text NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE public.sessions (session_key text PRIMARY KEY, data jsonb NOT NULL)"
            )
            connection.execute("GRANT USAGE ON SCHEMA public TO app,replicator")
            connection.execute(
                "GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO app"
            )
            connection.execute("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO app")
            connection.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO replicator")
    with psycopg.connect(SOURCE, autocommit=True) as source:
        source.execute("INSERT INTO notes(text) VALUES ('initial')")
        source.execute("INSERT INTO sessions VALUES ('browser-cookie', '{\"user_id\":7}')")


def connect_app(dsn):
    return psycopg.connect(
        make_conninfo(dsn, user="app", password="app-test-only"), autocommit=True
    )


def move():
    return PostgresMove(SOURCE, TARGET, REPLICA, local_testing=True, timeout=30)


def make_plan(driver):
    return driver.plan(
        source_environment_id=uuid4(),
        target_environment_id=uuid4(),
        writer_roles=["app"],
    )


def test_copy_cdc_restart_single_writer_sequence_and_session(tmp_path):
    workflow_id, run_id = uuid4(), uuid4()
    path = tmp_path / "run.json"
    with move() as driver:
        plan = make_plan(driver)
        with Journal(path, plan, workflow_id=workflow_id, run_id=run_id) as journal:
            driver.start(journal, digest(plan))
            driver.wait_ready(journal)
            with connect_app(SOURCE) as source:
                source.execute("INSERT INTO notes(text) VALUES ('during-copy')")
                source.execute('UPDATE sessions SET data=\'{"user_id":7,"saved":true}\'')
            with pytest.raises(psycopg.OperationalError):
                connect_app(TARGET)
    # New process objects recover the same publication, slot, subscription and plan.
    with (
        move() as driver,
        Journal(path, plan, workflow_id=workflow_id, run_id=run_id) as journal,
    ):
        driver.start(journal, digest(plan))
        driver.wait_ready(journal)
        result = driver.promote(journal, digest(plan))
        assert result["phase"] == "promoted" and result["validated"]
        with pytest.raises(psycopg.OperationalError):
            connect_app(SOURCE)
        with connect_app(TARGET) as target:
            assert target.execute("SELECT text FROM notes ORDER BY id").fetchall() == [
                ("initial",),
                ("during-copy",),
            ]
            assert target.execute(
                "SELECT data FROM sessions WHERE session_key=%s", ("browser-cookie",)
            ).fetchone()[0] == {"user_id": 7, "saved": True}
            assert (
                target.execute(
                    "INSERT INTO notes(text) VALUES ('new-writer') RETURNING id"
                ).fetchone()[0]
                == 3
            )
        with pytest.raises(MigrationError, match="자동 원복"):
            driver.abort_before_promotion(journal)
        # A lost promotion acknowledgement must never re-enable the old writer.
        journal.save("promotion_pending")
        assert driver.promote(journal, digest(plan))["phase"] == "promoted"
        driver.cleanup(journal)
        assert driver.cleanup(journal)["phase"] == "cleaned"
        assert driver.source.execute("SELECT count(*) FROM pg_replication_slots").fetchone()[0] == 0
        assert driver.target.execute("SELECT count(*) FROM pg_subscription").fetchone()[0] == 0


def test_bad_approval_or_plan_cannot_change_target(tmp_path):
    with move() as driver:
        plan = make_plan(driver)
        workflow_id, run_id = uuid4(), uuid4()
        with Journal(
            tmp_path / "run.json", plan, workflow_id=workflow_id, run_id=run_id
        ) as journal:
            with pytest.raises(MigrationError, match="승인"):
                driver.start(journal, "0" * 64)
            assert driver.target.execute(
                "SELECT rolcanlogin FROM pg_roles WHERE rolname='app'"
            ).fetchone()[0]
            with pytest.raises(MigrationError, match="이미 진행"):
                with Journal(tmp_path / "run.json", plan, workflow_id=workflow_id, run_id=run_id):
                    pass
        with pytest.raises(MigrationError, match="실행 ID"):
            with Journal(tmp_path / "run.json", plan, workflow_id=workflow_id, run_id=uuid4()):
                pass


def test_data_mismatch_stays_fenced_and_explicit_pre_promotion_abort(tmp_path):
    with move() as driver:
        plan = make_plan(driver)
        with Journal(tmp_path / "run.json", plan, workflow_id=uuid4(), run_id=uuid4()) as journal:
            driver.start(journal, digest(plan))
            driver.wait_ready(journal)
            driver.target.execute("UPDATE notes SET text='injected-target-write'")
            with pytest.raises(MigrationError, match="데이터 대조"):
                driver.promote(journal, digest(plan))
            assert journal.state["phase"] == "source_fenced"
            with pytest.raises(psycopg.OperationalError):
                connect_app(SOURCE)
            driver.abort_before_promotion(journal)
            with connect_app(SOURCE) as source:
                assert source.execute("SELECT text FROM notes").fetchone()[0] == "initial"
            with pytest.raises(psycopg.OperationalError):
                connect_app(TARGET)


def test_nonempty_target_unlisted_writer_or_schema_drift_are_rejected(tmp_path):
    with move() as driver:
        driver.target.execute("INSERT INTO notes(text) VALUES ('existing-customer-data')")
        with pytest.raises(MigrationError, match="데이터가 있습니다"):
            make_plan(driver)
        driver.target.execute("DELETE FROM notes")
        driver.source.execute("CREATE ROLE unexpected_writer LOGIN PASSWORD 'test-only'")
        driver.source.execute("GRANT USAGE ON SCHEMA public TO unexpected_writer")
        driver.source.execute("GRANT INSERT ON notes TO unexpected_writer")
        with pytest.raises(MigrationError, match="등록하지 않은"):
            make_plan(driver)
        driver.source.execute("REVOKE INSERT ON notes FROM unexpected_writer")
        plan = make_plan(driver)
        driver.target.execute("ALTER TABLE notes ADD COLUMN unexpected text")
        with Journal(tmp_path / "run.json", plan, workflow_id=uuid4(), run_id=uuid4()) as journal:
            with pytest.raises(MigrationError, match="스키마가 변경"):
                driver.start(journal, digest(plan))


def test_prepared_transaction_blocks_promotion_until_explicit_recovery(tmp_path):
    with move() as driver:
        plan = make_plan(driver)
        with Journal(tmp_path / "run.json", plan, workflow_id=uuid4(), run_id=uuid4()) as journal:
            driver.start(journal, digest(plan))
            driver.wait_ready(journal)
            with connect_app(SOURCE) as source:
                source.execute("BEGIN")
                source.execute("INSERT INTO notes(text) VALUES ('not-committed')")
                source.execute("PREPARE TRANSACTION 'delta-test-pending'")
            try:
                with pytest.raises(MigrationError, match="준비된 트랜잭션"):
                    driver.promote(journal, digest(plan))
                assert not driver.source.execute(
                    "SELECT rolcanlogin FROM pg_roles WHERE rolname='app'"
                ).fetchone()[0]
            finally:
                driver.source.execute("ROLLBACK PREPARED 'delta-test-pending'")
            driver.abort_before_promotion(journal)


def test_replication_role_cannot_have_app_write_privileges():
    with move() as driver:
        driver.source.execute("GRANT INSERT ON notes TO replicator")
        with pytest.raises(MigrationError, match="쓰기 권한"):
            make_plan(driver)
