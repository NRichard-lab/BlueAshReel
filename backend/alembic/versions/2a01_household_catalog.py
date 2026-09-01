"""Household assignments, watch state, and indexed local catalog.

Revision ID: 2a0100000001
Revises: 773863f5a6aa
"""

import sqlalchemy as sa

from alembic import op

revision: str = "2a0100000001"
down_revision: str | None = "773863f5a6aa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_libraries",
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("library_id", sa.String(36), sa.ForeignKey("libraries.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_index("ix_user_libraries_library_user", "user_libraries", ["library_id", "user_id"])
    op.execute(
        "INSERT INTO user_libraries SELECT DISTINCT u.id,l.id FROM users u JOIN user_roles ur ON ur.user_id=u.id JOIN roles r ON r.id=ur.role_id CROSS JOIN libraries l WHERE r.name='Owner'"
    )
    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("auto_next", sa.Boolean(), nullable=False),
        sa.Column("next_countdown", sa.Integer(), nullable=False),
    )
    op.create_table(
        "watch_progress",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("media_item_id", sa.String(36), sa.ForeignKey("media_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("media_file_id", sa.String(36), sa.ForeignKey("media_files.id", ondelete="SET NULL")),
        sa.Column("position_seconds", sa.Float(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("watched_seconds", sa.Float(), nullable=False),
        sa.Column("watched", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_played_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("user_id", "media_item_id", name="uq_progress_user_media"),
    )
    op.create_index("ix_watch_progress_user_id", "watch_progress", ["user_id"])
    op.create_index("ix_watch_progress_media_item_id", "watch_progress", ["media_item_id"])
    op.create_index("ix_progress_user_watched_last", "watch_progress", ["user_id", "watched", "last_played_at"])
    for name, type_ in [
        ("profile", sa.String(80)),
        ("level", sa.Integer()),
        ("pixel_format", sa.String(40)),
        ("bit_depth", sa.Integer()),
    ]:
        op.add_column("video_streams", sa.Column(name, type_, nullable=True))
    op.create_index("ix_media_library_kind_added", "media_items", ["library_id", "kind", "created_at"])
    # External-content FTS stays synchronized even when media is updated outside the scanner.
    op.execute(
        "CREATE VIRTUAL TABLE media_search USING fts5(title, year, content='media_items', content_rowid='rowid', tokenize='unicode61 remove_diacritics 2')"
    )
    op.execute(
        "CREATE TRIGGER media_search_insert AFTER INSERT ON media_items BEGIN INSERT INTO media_search(rowid,title,year) VALUES(new.rowid,new.title,new.year); END"
    )
    op.execute(
        "CREATE TRIGGER media_search_delete AFTER DELETE ON media_items BEGIN INSERT INTO media_search(media_search,rowid,title,year) VALUES('delete',old.rowid,old.title,old.year); END"
    )
    op.execute(
        "CREATE TRIGGER media_search_update AFTER UPDATE OF title,year ON media_items BEGIN INSERT INTO media_search(media_search,rowid,title,year) VALUES('delete',old.rowid,old.title,old.year); INSERT INTO media_search(rowid,title,year) VALUES(new.rowid,new.title,new.year); END"
    )
    op.execute("INSERT INTO media_search(media_search) VALUES('rebuild')")
    op.execute("PRAGMA optimize")


def downgrade() -> None:
    for name in ["insert", "delete", "update"]:
        op.execute(f"DROP TRIGGER media_search_{name}")
    op.execute("DROP TABLE media_search")
    op.drop_index("ix_media_library_kind_added", table_name="media_items")
    for name in ["profile", "level", "pixel_format", "bit_depth"]:
        op.drop_column("video_streams", name)
    op.drop_table("watch_progress")
    op.drop_table("user_preferences")
    op.drop_table("user_libraries")
