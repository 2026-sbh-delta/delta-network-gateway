from uuid import uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from delta_platform.core.database import ready


def test_existing_revision_0013_keeps_reservations(engine):
    target_id = f"rollback-test-{uuid4()}"
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO release_reservations (target_id, service_id, config_hash) "
                "VALUES (:target, :service, :hash)"
            ),
            {"target": target_id, "service": uuid4(), "hash": "a" * 64},
        )
    try:
        command.upgrade(Config("alembic.ini"), "head")
        assert ready(engine)
        with engine.connect() as conn:
            assert (
                conn.scalar(
                    sa.text("SELECT count(*) FROM release_reservations WHERE target_id = :target"),
                    {"target": target_id},
                )
                == 1
            )
    finally:
        with engine.begin() as conn:
            conn.execute(
                sa.text("DELETE FROM release_reservations WHERE target_id = :target"),
                {"target": target_id},
            )
