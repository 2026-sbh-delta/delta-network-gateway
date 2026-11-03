"""고정 빌드 커밋의 SAST 결과 연결"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("run_kind", "runs", type_="check")
    op.create_check_constraint(
        "run_kind",
        "runs",
        "kind IN ('source_preflight','image_build','sandbox_check','security_check')",
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM runs WHERE kind = 'security_check'")):
        raise RuntimeError("보안 검사 기록이 있어 자동 downgrade하지 않습니다.")
    op.drop_constraint("run_kind", "runs", type_="check")
    op.create_check_constraint(
        "run_kind", "runs", "kind IN ('source_preflight','image_build','sandbox_check')"
    )
