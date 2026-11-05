"""Use the existing leased job queue for on-premise application removal."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def constraints(include_removal):
    kinds = "'source_preflight', 'image_build', 'sandbox_check', 'security_check', 'dast_check'"
    if include_removal:
        kinds += ", 'runtime_remove'"
    sha = (
        "(resolved_commit_sha IS NOT NULL OR kind = 'runtime_remove')"
        if include_removal
        else "resolved_commit_sha IS NOT NULL"
    )
    op.create_check_constraint("run_kind", "runs", f"kind IN ({kinds})")
    op.create_check_constraint(
        "run_outcome",
        "runs",
        "(status IN ('queued','running') AND finished_at IS NULL AND result IS NULL AND error IS NULL) OR "
        f"(status = 'succeeded' AND finished_at IS NOT NULL AND {sha} AND result IS NOT NULL AND error IS NULL) OR "
        "(status = 'failed' AND finished_at IS NOT NULL AND error IS NOT NULL AND result IS NULL)",
    )


def upgrade():
    op.drop_constraint("run_kind", "runs", type_="check")
    op.drop_constraint("run_outcome", "runs", type_="check")
    constraints(True)


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM runs WHERE kind='runtime_remove'")):
        raise RuntimeError("앱 삭제 이력이 있어 자동 downgrade하지 않습니다.")
    op.drop_constraint("run_kind", "runs", type_="check")
    op.drop_constraint("run_outcome", "runs", type_="check")
    constraints(False)
