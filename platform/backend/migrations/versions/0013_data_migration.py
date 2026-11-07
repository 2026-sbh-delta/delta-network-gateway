"""Recognize migration run records already present in production."""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None
KINDS = "'source_preflight','image_build','sandbox_check','security_check','dast_check','runtime_remove','service_deploy','service_remove','data_migrate'"


def upgrade():
    op.drop_constraint("run_kind", "runs", type_="check")
    op.create_check_constraint("run_kind", "runs", f"kind IN ({KINDS})")


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM runs WHERE kind = 'data_migrate'")):
        raise RuntimeError("Migration run history must be preserved")
    op.drop_constraint("run_kind", "runs", type_="check")
    op.create_check_constraint("run_kind", "runs", f"kind IN ({KINDS.rsplit(',', 1)[0]})")
