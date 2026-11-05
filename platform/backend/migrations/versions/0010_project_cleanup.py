"""Keep project ownership until resource cleanup completes."""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None
KINDS = "'source_preflight','image_build','sandbox_check','security_check','dast_check','runtime_remove','service_deploy'"


def upgrade():
    op.add_column(
        "services",
        sa.Column("deletion_requested", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.drop_constraint("run_kind", "runs", type_="check")
    op.create_check_constraint("run_kind", "runs", f"kind IN ({KINDS},'service_remove')")


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM runs WHERE kind = 'service_remove'")):
        raise RuntimeError("Project cleanup history exists")
    op.drop_constraint("run_kind", "runs", type_="check")
    op.create_check_constraint("run_kind", "runs", f"kind IN ({KINDS})")
    op.drop_column("services", "deletion_requested")
