"""Preserve the reservation table used by the deployed schema."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "release_reservations",
        sa.Column("target_id", sa.String(128), primary_key=True),
        sa.Column("service_id", UUID(as_uuid=True), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM release_reservations")):
        raise RuntimeError("Reservation data must be preserved")
    op.drop_table("release_reservations")
