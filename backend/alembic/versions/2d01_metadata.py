"""Add local normalized metadata and provider artwork without replacing catalog tables."""

import sqlalchemy as sa

from alembic import op

revision: str = "2d0100000001"
down_revision: str | None = "2c0100000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "metadata_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("media_item_id", sa.String(36), sa.ForeignKey("media_items.id", ondelete="CASCADE"), unique=True),
        sa.Column("season_id", sa.String(36), sa.ForeignKey("seasons.id", ondelete="CASCADE"), unique=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(32)),
        sa.Column("provider_id", sa.String(100)),
        sa.Column("external_ids", sa.JSON(), nullable=False),
        sa.Column("title", sa.String(500)),
        sa.Column("original_title", sa.String(500)),
        sa.Column("year", sa.Integer()),
        sa.Column("release_date", sa.String(10)),
        sa.Column("last_air_date", sa.String(10)),
        sa.Column("runtime_seconds", sa.Integer()),
        sa.Column("overview", sa.Text()),
        sa.Column("tagline", sa.String(1000)),
        sa.Column("original_language", sa.String(32)),
        sa.Column("content_rating", sa.String(40)),
        *(
            sa.Column(name, sa.JSON(), nullable=False)
            for name in ("genres", "studios", "networks", "creators", "countries", "credits", "related_provider_ids")
        ),
        sa.Column("series_status", sa.String(80)),
        sa.Column("number_of_seasons", sa.Integer()),
        sa.Column("number_of_episodes", sa.Integer()),
        sa.Column("vote_average", sa.Float()),
        sa.Column("vote_count", sa.Integer()),
        sa.Column("match_confidence", sa.Float(), nullable=False),
        sa.Column("match_method", sa.String(80)),
        sa.Column("manually_confirmed", sa.Boolean(), nullable=False),
        *(
            sa.Column(name, sa.DateTime(timezone=True))
            for name in ("matched_at", "metadata_updated_at", "provider_data_updated_at", "attempted_at")
        ),
        sa.Column("error_code", sa.String(80)),
        sa.Column("auto_match_enabled", sa.Boolean(), nullable=False),
        sa.CheckConstraint("(media_item_id IS NULL) != (season_id IS NULL)", name="ck_metadata_owner"),
        sa.CheckConstraint(
            "status IN ('unavailable','unmatched','needs_review','matched','fetching','complete','error')",
            name="ck_metadata_status",
        ),
    )
    op.create_index("ix_metadata_provider_item", "metadata_records", ["provider", "kind", "provider_id"])
    op.add_column("local_artwork", sa.Column("provider", sa.String(32)))
    op.add_column("local_artwork", sa.Column("provider_path", sa.String(500)))
    op.add_column("local_artwork", sa.Column("content_type", sa.String(32)))


def downgrade() -> None:
    # No catalog/history/identity rows are removed by rollback.
    op.drop_column("local_artwork", "content_type")
    op.drop_column("local_artwork", "provider_path")
    op.drop_column("local_artwork", "provider")
    op.drop_table("metadata_records")
