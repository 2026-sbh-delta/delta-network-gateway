import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = sa.MetaData()


def col(name, type_, **kwargs):
    return sa.Column(name, type_, **kwargs)


def timestamp(name, **kwargs):
    return col(name, sa.DateTime(timezone=True), **kwargs)


sessions = sa.Table(
    "sessions",
    metadata,
    col("token_hash", sa.String(64), primary_key=True),
    col("csrf_token", sa.String(64), nullable=False),
    col("credential_version", sa.String(64), nullable=False),
    timestamp("created_at", nullable=False, server_default=sa.func.now()),
    timestamp("expires_at", nullable=False, index=True),
)
rate_limits = sa.Table(
    "auth_rate_limits",
    metadata,
    col("bucket_key", sa.String(64), primary_key=True),
    timestamp("window_start", nullable=False),
    col("failures", sa.Integer, nullable=False),
    timestamp("blocked_until"),
)
projects = sa.Table(
    "projects",
    metadata,
    col("id", UUID(as_uuid=True), primary_key=True),
    col("name", sa.String(80), nullable=False),
)
services = sa.Table(
    "services",
    metadata,
    col("id", UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "project_id",
        UUID(as_uuid=True),
        sa.ForeignKey("projects.id"),
        nullable=False,
        server_default=sa.text("'496562be-f67b-5f7e-a16b-929fd347b9ad'::uuid"),
    ),
    col("name", sa.String(80), nullable=False, unique=True),
    col("repository_url", sa.String(512), nullable=False),
    col("repo_owner", sa.String(100), nullable=False),
    col("repo_name", sa.String(100), nullable=False),
    col("source_ref", sa.String(255), nullable=False),
    col("build_context", sa.String(256), nullable=False),
    col("dockerfile_path", sa.String(256), nullable=False),
    col("container_port", sa.Integer, nullable=False),
    col("healthcheck_path", sa.String(256), nullable=False),
    col("deployment_target", JSONB(none_as_null=True)),
    col("application", JSONB(none_as_null=True)),
    col("parent_service_id", UUID(as_uuid=True)),
    col("deletion_requested", sa.Boolean, nullable=False, server_default=sa.false()),
    col("request_key", sa.String(128), nullable=False, unique=True),
    col("request_hash", sa.String(64), nullable=False),
    timestamp("created_at", nullable=False, server_default=sa.func.now()),
    sa.CheckConstraint("container_port BETWEEN 1 AND 65535", name="valid_port"),
    sa.UniqueConstraint("id", "project_id", name="service_project_identity"),
)
environments = sa.Table(
    "service_environments",
    metadata,
    col("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("service_id", UUID(as_uuid=True), sa.ForeignKey("services.id"), nullable=False),
    col("project_id", UUID(as_uuid=True), nullable=False),
    col("name", sa.String(80), nullable=False),
    col("cloud", sa.String(16), nullable=False),
    col("region", sa.String(64), nullable=False),
    col("version", sa.String(128)),
    col("binding", JSONB, nullable=False),
    timestamp("created_at", nullable=False, server_default=sa.func.now()),
    timestamp("updated_at", nullable=False, server_default=sa.func.now()),
    sa.UniqueConstraint("service_id", "name", name="environment_name_unique"),
    sa.CheckConstraint("cloud IN ('aws','gcp','onprem')", name="environment_cloud"),
    sa.ForeignKeyConstraint(
        ["service_id", "project_id"],
        ["services.id", "services.project_id"],
        name="environment_service_project",
    ),
)
workflows = sa.Table(
    "workflows",
    metadata,
    col("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
    sa.Column("service_id", UUID(as_uuid=True), sa.ForeignKey("services.id"), nullable=False),
    col("kind", sa.String(24), nullable=False),
    col("status", sa.String(24), nullable=False),
    col("input_snapshot", JSONB, nullable=False),
    col("input_hash", sa.String(64), nullable=False),
    col("request_key", sa.String(128), nullable=False),
    col("approved_by", sa.String(128)),
    timestamp("approved_at"),
    timestamp("created_at", nullable=False, server_default=sa.func.now()),
    sa.UniqueConstraint("service_id", "request_key", name="workflow_request_unique"),
    sa.ForeignKeyConstraint(
        ["service_id", "project_id"],
        ["services.id", "services.project_id"],
        name="workflow_service_project",
    ),
    sa.CheckConstraint("kind IN ('deployment','migration')", name="workflow_kind"),
    sa.CheckConstraint(
        "status IN ('planned','approved','running','succeeded','failed')", name="workflow_status"
    ),
)
workflow_executions = sa.Table(
    "workflow_executions",
    metadata,
    col("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("workflow_id", UUID(as_uuid=True), sa.ForeignKey("workflows.id"), nullable=False),
    sa.Column(
        "environment_id",
        UUID(as_uuid=True),
        sa.ForeignKey("service_environments.id"),
        nullable=False,
    ),
    col("kind", sa.String(40), nullable=False),
    col("payload", JSONB, nullable=False),
    col("payload_hash", sa.String(64), nullable=False),
    col("request_key", sa.String(128), nullable=False),
    col("status", sa.String(24), nullable=False, server_default="prepared"),
    col("result", JSONB),
    timestamp("created_at", nullable=False, server_default=sa.func.now()),
    sa.UniqueConstraint("workflow_id", "request_key", name="execution_request_unique"),
)
runs = sa.Table(
    "runs",
    metadata,
    col("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("service_id", UUID(as_uuid=True), sa.ForeignKey("services.id"), nullable=False),
    col("kind", sa.String(40), nullable=False, server_default="source_preflight"),
    col("input_snapshot", JSONB(none_as_null=True), nullable=False),
    col("request_key", sa.String(128), nullable=False),
    col("request_hash", sa.String(64), nullable=False),
    col("status", sa.String(16), nullable=False),
    col("current_step", sa.String(40)),
    col("resolved_commit_sha", sa.String(40)),
    col("cancel_requested", sa.Boolean, nullable=False, server_default=sa.false()),
    col("external_id", sa.String(255)),
    col("submission", JSONB(none_as_null=True)),
    col("attempt", sa.Integer, nullable=False, server_default="0"),
    timestamp("available_at", nullable=False, server_default=sa.func.now()),
    col("lease_token", UUID(as_uuid=True)),
    timestamp("lease_expires_at"),
    col("result", JSONB(none_as_null=True)),
    col("error", JSONB(none_as_null=True)),
    timestamp("created_at", nullable=False, server_default=sa.func.now()),
    timestamp("started_at"),
    timestamp("finished_at"),
    sa.UniqueConstraint("service_id", "kind", "request_key", name="run_request_unique"),
    sa.CheckConstraint(
        "kind IN ('source_preflight', 'image_build', 'sandbox_check', 'security_check', 'dast_check', 'runtime_remove', 'service_deploy', 'service_remove')",
        name="run_kind",
    ),
    sa.CheckConstraint("attempt >= 0", name="run_attempt"),
    sa.CheckConstraint("status IN ('queued','running','succeeded','failed')", name="run_status"),
    sa.CheckConstraint(
        "(status = 'running' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR (status <> 'running' AND lease_token IS NULL AND lease_expires_at IS NULL)",
        name="run_lease",
    ),
    sa.CheckConstraint(
        "(status IN ('queued','running') AND finished_at IS NULL AND result IS NULL AND error IS NULL) OR (status = 'succeeded' AND finished_at IS NOT NULL AND (resolved_commit_sha IS NOT NULL OR kind = 'runtime_remove') AND result IS NOT NULL AND error IS NULL) OR (status = 'failed' AND finished_at IS NOT NULL AND error IS NOT NULL AND result IS NULL)",
        name="run_outcome",
    ),
)
sa.Index(
    "one_active_run_per_service",
    runs.c.service_id,
    unique=True,
    postgresql_where=sa.and_(
        runs.c.status.in_(["queued", "running"]), runs.c.kind != "service_deploy"
    ),
)
sa.Index("run_queue", runs.c.status, runs.c.available_at)
sa.Index("run_history", runs.c.service_id, runs.c.created_at, runs.c.id)
events = sa.Table(
    "run_events",
    metadata,
    sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("runs.id"), primary_key=True),
    col("sequence", sa.Integer, primary_key=True),
    col("event_type", sa.String(40), nullable=False),
    col("step", sa.String(40)),
    col("message", sa.Text, nullable=False),
    col("details", JSONB(none_as_null=True), nullable=False),
    timestamp("created_at", nullable=False, server_default=sa.func.now()),
)

sa.Index(
    "one_active_sandbox_slot",
    sa.case((runs.c.kind.in_(("sandbox_check", "dast_check")), 1), else_=0),
    unique=True,
    postgresql_where=sa.and_(
        runs.c.kind.in_(("sandbox_check", "dast_check")), runs.c.status.in_(["queued", "running"])
    ),
)


deployment_plans = sa.Table(
    "deployment_plans",
    metadata,
    col("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("service_id", UUID(as_uuid=True), sa.ForeignKey("services.id"), nullable=False),
    col("kind", sa.String(24), nullable=False),
    col("request_key", sa.String(128), nullable=False),
    col("request_hash", sa.String(64), nullable=False),
    col("input_snapshot", JSONB, nullable=False),
    col("input_hash", sa.String(64), nullable=False),
    col("approved_by", sa.String(128)),
    timestamp("approved_at"),
    timestamp("created_at", nullable=False, server_default=sa.func.now()),
    sa.UniqueConstraint("service_id", "request_key", name="deployment_request_unique"),
    sa.CheckConstraint("kind IN ('deployment','migration')", name="deployment_kind"),
)
sa.Index(
    "one_active_deployment_target",
    runs.c.input_snapshot["environment_id"].astext,
    unique=True,
    postgresql_where=sa.and_(
        runs.c.kind == "service_deploy", runs.c.status.in_(["queued", "running"])
    ),
)
