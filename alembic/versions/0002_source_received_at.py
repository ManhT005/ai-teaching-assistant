"""Preserve original event time without replacing the server intake timestamp."""
from alembic import op
import sqlalchemy as sa

revision = "0002_source_received_at"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("events", sa.Column("source_received_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("events", "source_received_at")
