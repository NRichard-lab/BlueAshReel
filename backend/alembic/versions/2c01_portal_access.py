"""Local Portal grants and random remote aliases; preserve all existing media data."""

import sqlalchemy as sa

from alembic import op

revision: str = "2c0100000001"
down_revision: str | None = "2b0100000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portal_grants",
        sa.Column("portal_user_id", sa.String(36), primary_key=True),
        sa.Column("agent_id", sa.String(36), nullable=False),
        sa.Column("local_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("access_version", sa.Integer(), nullable=False),
    )
    op.create_table(
        "remote_objects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("agent_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("local_id", sa.String(36), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("agent_id", "kind", "local_id", name="uq_remote_object"),
    )


def downgrade() -> None:
    op.drop_table("remote_objects")
    op.drop_table("portal_grants")
