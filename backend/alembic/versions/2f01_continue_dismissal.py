"""Persist Continue Watching dismissals without erasing playback history."""

import sqlalchemy as sa

from alembic import op

revision: str = "2f0100000001"
down_revision: str | None = "2e0100000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("watch_progress") as batch:
        batch.add_column(sa.Column("continue_dismissed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("watch_progress") as batch:
        batch.drop_column("continue_dismissed_at")
