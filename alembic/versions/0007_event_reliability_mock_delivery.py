"""Add event leases, transactional outbox, mock receipts and rate limits."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_event_reliability"
down_revision = "0006_grounded_generation"
branch_labels = None
depends_on = None


def upgrade():
    for name, type_ in (
        ("lease_token", sa.String(36)),
        ("lease_expires_at", sa.DateTime(timezone=True)),
        ("worker_id", sa.String(120)),
        ("next_attempt_at", sa.DateTime(timezone=True)),
        ("error_category", sa.String(60)),
    ):
        op.add_column("events", sa.Column(name, type_, nullable=True))
    op.create_index(
        "ix_events_due",
        "events",
        ["status", "next_attempt_at", "received_at", "id"],
    )
    op.create_index("ix_events_lease_expiry", "events", ["lease_expires_at", "id"])
    # Grace period lets a pre-migration worker finish before orphan recovery.
    op.execute(
        "UPDATE events SET lease_expires_at = now() + interval '120 seconds' "
        "WHERE status = 'PROCESSING' AND lease_token IS NULL AND lease_expires_at IS NULL"
    )

    op.create_table(
        "outbox_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("message_key", sa.String(160), nullable=False),
        sa.Column("channel", sa.String(30), nullable=False),
        sa.Column("group_id", sa.String(160), nullable=False),
        sa.Column("class_id", sa.Integer(), sa.ForeignKey("classes.id"), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), server_default="PENDING", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("provider_message_id", sa.String(160)),
        sa.Column("last_error_code", sa.String(80)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("event_id", name="uq_outbox_event"),
        sa.UniqueConstraint("message_key", name="uq_outbox_message_key"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'SENDING', 'RETRY_WAIT', 'SENT', 'DEAD', 'SUPPRESSED')",
            name="ck_outbox_status",
        ),
    )
    op.create_index("ix_outbox_due", "outbox_messages", ["status", "next_attempt_at", "id"])
    op.create_index("ix_outbox_lease_expiry", "outbox_messages", ["lease_expires_at", "id"])

    op.create_table(
        "mock_delivered_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("outbox_id", sa.Integer(), sa.ForeignKey("outbox_messages.id"), nullable=False),
        sa.Column("message_key", sa.String(160), nullable=False),
        sa.Column("channel", sa.String(30), nullable=False),
        sa.Column("group_id", sa.String(160), nullable=False),
        sa.Column("class_id", sa.Integer(), sa.ForeignKey("classes.id"), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("citations", postgresql.JSONB(), nullable=False),
        sa.Column(
            "delivered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("outbox_id", name="uq_mock_delivery_outbox"),
        sa.UniqueConstraint("message_key", name="uq_mock_delivery_message_key"),
    )

    op.create_table(
        "rate_limit_windows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope_type", sa.String(20), nullable=False),
        sa.Column("scope_key", sa.String(64), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), server_default="0", nullable=False),
        sa.UniqueConstraint("scope_type", "scope_key", "window_start", name="uq_rate_window"),
        sa.CheckConstraint("scope_type IN ('sender', 'group')", name="ck_rate_scope_type"),
    )
    op.create_index("ix_rate_window_start", "rate_limit_windows", ["window_start"])


def downgrade():
    op.drop_index("ix_rate_window_start", table_name="rate_limit_windows")
    op.drop_table("rate_limit_windows")
    op.drop_table("mock_delivered_messages")
    op.drop_index("ix_outbox_lease_expiry", table_name="outbox_messages")
    op.drop_index("ix_outbox_due", table_name="outbox_messages")
    op.drop_table("outbox_messages")
    op.drop_index("ix_events_lease_expiry", table_name="events")
    op.drop_index("ix_events_due", table_name="events")
    for name in (
        "error_category",
        "next_attempt_at",
        "worker_id",
        "lease_expires_at",
        "lease_token",
    ):
        op.drop_column("events", name)
