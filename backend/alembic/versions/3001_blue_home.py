"""Map central Blue Home profile IDs without rewriting existing watch state."""

import sqlalchemy as sa

from alembic import op

revision: str = "300100000001"
down_revision: str | None = "2f0100000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "blue_home_state",
        sa.Column("profile_id", sa.String(36), primary_key=True),
        sa.Column("home_id", sa.String(36), nullable=False),
        sa.Column("owner_account_id", sa.String(36), nullable=False),
        sa.Column("local_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, unique=True),
    )


def downgrade() -> None:
    op.drop_table("blue_home_state")
