"""샌드박스 실행 확인과 중단 요청"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("run_kind", "runs", type_="check")
    op.create_check_constraint(
        "run_kind", "runs", "kind IN ('source_preflight','image_build','sandbox_check')"
    )
    op.add_column(
        "runs",
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "one_active_sandbox_slot",
        "runs",
        ["kind"],
        unique=True,
        postgresql_where=sa.text("kind = 'sandbox_check' AND status IN ('queued','running')"),
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM runs WHERE kind = 'sandbox_check'")):
        raise RuntimeError("샌드박스 기록이 있어 이전 스키마로 자동 복구하지 않습니다.")
    op.drop_index("one_active_sandbox_slot", table_name="runs")
    op.drop_column("runs", "cancel_requested")
    op.drop_constraint("run_kind", "runs", type_="check")
    op.create_check_constraint("run_kind", "runs", "kind IN ('source_preflight','image_build')")
