"""Persist operator-owned service/environment bindings, without raw telemetry."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "service_environments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("service_id", UUID(as_uuid=True), sa.ForeignKey("services.id"), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("cloud", sa.String(16), nullable=False),
        sa.Column("region", sa.String(64), nullable=False),
        sa.Column("version", sa.String(128)),
        sa.Column("binding", JSONB, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("service_id", "name", name="environment_name_unique"),
        sa.CheckConstraint("cloud IN ('aws','gcp','onprem')", name="environment_cloud"),
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM service_environments")):
        raise RuntimeError("환경 연결 기록이 있어 자동 downgrade하지 않습니다.")
    op.drop_table("service_environments")
