"""Stable workspace identity and immutable, approved execution inputs."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None
WORKSPACE = "496562be-f67b-5f7e-a16b-929fd347b9ad"


def upgrade():
    op.create_table(
        "projects",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
    )
    op.execute(
        sa.text(
            "INSERT INTO projects(id,name) VALUES (CAST(:id AS uuid),'팀 공통 작업 공간')"
        ).bindparams(id=WORKSPACE)
    )
    op.add_column(
        "services",
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text(f"'{WORKSPACE}'::uuid"),
        ),
    )
    op.create_foreign_key("services_project", "services", "projects", ["project_id"], ["id"])
    op.create_unique_constraint("service_project_identity", "services", ["id", "project_id"])
    op.create_foreign_key(
        "environment_service_project",
        "service_environments",
        "services",
        ["service_id", "project_id"],
        ["id", "project_id"],
    )
    op.create_table(
        "workflows",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("service_id", UUID(as_uuid=True), sa.ForeignKey("services.id"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("input_snapshot", JSONB, nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("request_key", sa.String(128), nullable=False),
        sa.Column("approved_by", sa.String(128)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("service_id", "request_key", name="workflow_request_unique"),
        sa.ForeignKeyConstraint(
            ["service_id", "project_id"],
            ["services.id", "services.project_id"],
            name="workflow_service_project",
        ),
        sa.CheckConstraint("kind IN ('deployment','migration')", name="workflow_kind"),
        sa.CheckConstraint(
            "status IN ('planned','approved','running','succeeded','failed')",
            name="workflow_status",
        ),
    )
    op.create_table(
        "workflow_executions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("workflow_id", UUID(as_uuid=True), sa.ForeignKey("workflows.id"), nullable=False),
        sa.Column(
            "environment_id",
            UUID(as_uuid=True),
            sa.ForeignKey("service_environments.id"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("request_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="prepared"),
        sa.Column("result", JSONB),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("workflow_id", "request_key", name="execution_request_unique"),
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM workflows")):
        raise RuntimeError("배포·이전 workflow 기록이 있어 자동 downgrade하지 않습니다.")
    op.drop_table("workflow_executions")
    op.drop_table("workflows")
    op.drop_constraint("environment_service_project", "service_environments", type_="foreignkey")
    op.drop_constraint("service_project_identity", "services", type_="unique")
    op.drop_constraint("services_project", "services", type_="foreignkey")
    op.drop_column("services", "project_id")
    op.drop_table("projects")
