"""DAST execution shares the one owned sandbox slot."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("run_kind", "runs", type_="check")
    op.create_check_constraint(
        "run_kind",
        "runs",
        "kind IN ('source_preflight','image_build','sandbox_check','security_check','dast_check')",
    )
    op.drop_index("one_active_sandbox_slot", table_name="runs")
    op.execute(
        "CREATE UNIQUE INDEX one_active_sandbox_slot ON runs ((CASE WHEN kind IN ('sandbox_check','dast_check') THEN 1 ELSE 0 END)) WHERE kind IN ('sandbox_check','dast_check') AND status IN ('queued','running')"
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM runs WHERE kind = 'dast_check'")):
        raise RuntimeError("DAST 기록이 있어 자동 downgrade하지 않습니다.")
    op.drop_index("one_active_sandbox_slot", table_name="runs")
    op.create_index(
        "one_active_sandbox_slot",
        "runs",
        ["kind"],
        unique=True,
        postgresql_where=sa.text("kind = 'sandbox_check' AND status IN ('queued','running')"),
    )
    op.drop_constraint("run_kind", "runs", type_="check")
    op.create_check_constraint(
        "run_kind",
        "runs",
        "kind IN ('source_preflight','image_build','sandbox_check','security_check')",
    )
