"""Group components and persist the automatic registration workflow."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("services", sa.Column("application", JSONB(none_as_null=True)))
    op.add_column("services", sa.Column("parent_service_id", UUID(as_uuid=True)))
    op.create_index("application_children", "services", ["parent_service_id"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM services WHERE application IS NOT NULL")):
        raise RuntimeError("Application workflow history exists")
    op.drop_index("application_children", "services")
    op.drop_column("services", "parent_service_id")
    op.drop_column("services", "application")
