"""Immutable deployment plans and independently tracked destination runs."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None
KINDS = "'source_preflight','image_build','sandbox_check','security_check','dast_check','runtime_remove'"


def upgrade():
    op.add_column("services", sa.Column("deployment_target", JSONB(none_as_null=True)))
    op.create_table(
        "deployment_plans",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("service_id", UUID(as_uuid=True), sa.ForeignKey("services.id"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("request_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("input_snapshot", JSONB, nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("approved_by", sa.String(128)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("service_id", "request_key", name="deployment_request_unique"),
        sa.CheckConstraint("kind IN ('deployment','migration')", name="deployment_kind"),
    )
    op.drop_constraint("run_kind", "runs", type_="check")
    op.create_check_constraint("run_kind", "runs", f"kind IN ({KINDS},'service_deploy')")
    # Existing admissions serialize on the service row and check all active runs.
    # Only the deployment batch admission may create two jobs under that same lock.
    op.drop_index("one_active_run_per_service", table_name="runs")
    op.create_index(
        "one_active_run_per_service",
        "runs",
        ["service_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running') AND kind <> 'service_deploy'"),
    )
    op.create_index(
        "one_active_deployment_target",
        "runs",
        [sa.text("(input_snapshot ->> 'environment_id')")],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running') AND kind = 'service_deploy'"),
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM deployment_plans")):
        raise RuntimeError("배포 이력이 있어 자동 downgrade하지 않습니다.")
    op.drop_index("one_active_deployment_target", table_name="runs")
    op.drop_index("one_active_run_per_service", table_name="runs")
    op.create_index(
        "one_active_run_per_service",
        "runs",
        ["service_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running')"),
    )
    op.drop_constraint("run_kind", "runs", type_="check")
    op.create_check_constraint("run_kind", "runs", f"kind IN ({KINDS})")
    op.drop_table("deployment_plans")
    op.drop_column("services", "deployment_target")
