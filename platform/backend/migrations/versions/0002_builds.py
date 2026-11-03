"""이미지 빌드와 외부 실행 복구 정보"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("run_kind", "runs", type_="check")
    op.create_check_constraint("run_kind", "runs", "kind IN ('source_preflight','image_build')")
    op.add_column("runs", sa.Column("external_id", sa.String(255)))
    op.add_column("runs", sa.Column("submission", JSONB(none_as_null=True)))


def downgrade():
    # Do not silently delete build evidence to satisfy the old constraint.
    conn = op.get_bind()
    if conn.scalar(sa.text("SELECT count(*) FROM runs WHERE kind = 'image_build'")):
        raise RuntimeError("빌드 기록이 있어 이전 스키마로 자동 복구하지 않습니다.")
    op.drop_column("runs", "submission")
    op.drop_column("runs", "external_id")
    op.drop_constraint("run_kind", "runs", type_="check")
    op.create_check_constraint("run_kind", "runs", "kind = 'source_preflight'")
