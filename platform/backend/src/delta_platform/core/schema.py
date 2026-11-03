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
services = sa.Table(
    "services",
    metadata,
    col("id", UUID(as_uuid=True), primary_key=True),
    col("name", sa.String(80), nullable=False, unique=True),
    col("repository_url", sa.String(512), nullable=False),
    col("repo_owner", sa.String(100), nullable=False),
    col("repo_name", sa.String(100), nullable=False),
    col("source_ref", sa.String(255), nullable=False),
    col("build_context", sa.String(256), nullable=False),
    col("dockerfile_path", sa.String(256), nullable=False),
    col("container_port", sa.Integer, nullable=False),
    col("healthcheck_path", sa.String(256), nullable=False),
    col("request_key", sa.String(128), nullable=False, unique=True),
    col("request_hash", sa.String(64), nullable=False),
    timestamp("created_at", nullable=False, server_default=sa.func.now()),
    sa.CheckConstraint("container_port BETWEEN 1 AND 65535", name="valid_port"),
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
    sa.CheckConstraint("kind IN ('source_preflight', 'image_build')", name="run_kind"),
    sa.CheckConstraint("attempt >= 0", name="run_attempt"),
    sa.CheckConstraint("status IN ('queued','running','succeeded','failed')", name="run_status"),
    sa.CheckConstraint(
        "(status = 'running' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR (status <> 'running' AND lease_token IS NULL AND lease_expires_at IS NULL)",
        name="run_lease",
    ),
    sa.CheckConstraint(
        "(status IN ('queued','running') AND finished_at IS NULL AND result IS NULL AND error IS NULL) OR (status = 'succeeded' AND finished_at IS NOT NULL AND resolved_commit_sha IS NOT NULL AND result IS NOT NULL AND error IS NULL) OR (status = 'failed' AND finished_at IS NOT NULL AND error IS NOT NULL AND result IS NULL)",
        name="run_outcome",
    ),
)
sa.Index(
    "one_active_run_per_service",
    runs.c.service_id,
    unique=True,
    postgresql_where=runs.c.status.in_(["queued", "running"]),
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
