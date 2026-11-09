"""Persist bounded, retry-stable contextual query resolutions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_contextual_teaching"
down_revision = "0007_event_reliability"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "event_context_resolutions",
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("prior_event_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("resolution_status", sa.String(20), nullable=False),
        sa.Column("intent", sa.String(30), nullable=False),
        sa.Column("resolved_query", sa.String(700), nullable=False),
        sa.Column("original_query_hash", sa.String(64), nullable=False),
        sa.Column("rewriter_revision", sa.String(40), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "jsonb_array_length(prior_event_ids) <= 5", name="ck_context_prior_count"
        ),
        sa.CheckConstraint(
            "resolution_status IN ('STANDALONE', 'RESOLVED', 'AMBIGUOUS', 'NO_CONTEXT', 'INVALID')",
            name="ck_context_status",
        ),
        sa.CheckConstraint("length(resolved_query) <= 700", name="ck_context_query_length"),
    )


def downgrade():
    op.drop_table("event_context_resolutions")
