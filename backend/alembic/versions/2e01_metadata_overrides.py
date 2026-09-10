"""Add sparse presentation metadata and artwork selections."""

import sqlalchemy as sa

from alembic import op

revision = "2e0100000001"
down_revision = "2d0100000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("metadata_records") as batch:
        batch.add_column(sa.Column("field_overrides", sa.JSON(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("artwork_selections", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    with op.batch_alter_table("metadata_records") as batch:
        batch.drop_column("artwork_selections")
        batch.drop_column("field_overrides")
