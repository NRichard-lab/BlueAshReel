"""Authenticated playback sessions and durable checkpoints."""

import sqlalchemy as sa

from alembic import op

revision: str = "2b0100000001"
down_revision: str | None = "2a0100000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "playback_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("auth_session_id", sa.String(36), sa.ForeignKey("user_sessions.id", ondelete="SET NULL")),
        sa.Column("media_item_id", sa.String(36), sa.ForeignKey("media_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("media_file_id", sa.String(36), sa.ForeignKey("media_files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("decision", sa.JSON(), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("audio_index", sa.Integer()),
        sa.Column("subtitle_index", sa.Integer()),
        sa.Column("quality", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("position_seconds", sa.Float(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("watched_seconds", sa.Float(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("was_playing", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("startup_ms", sa.Integer()),
        sa.Column("error", sa.String(200)),
    )
    op.create_index("ix_playback_sessions_user_id", "playback_sessions", ["user_id"])
    op.create_index("ix_playback_sessions_media_item_id", "playback_sessions", ["media_item_id"])
    op.create_index("ix_playback_user_state_seen", "playback_sessions", ["user_id", "state", "last_seen_at"])
    op.create_index("ix_playback_user_media_started", "playback_sessions", ["user_id", "media_item_id", "started_at"])
    op.create_index("ix_playback_state_seen", "playback_sessions", ["state", "last_seen_at"])


def downgrade() -> None:
    op.drop_table("playback_sessions")
