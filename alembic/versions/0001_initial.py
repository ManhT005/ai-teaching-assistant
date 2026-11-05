"""Initialize Lean MVP storage and pgvector.

Revision ID: 0001_initial
Revises:
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from pgvector.sqlalchemy import Vector

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table("classes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(60), nullable=False, unique=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("bot_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table("channel_bindings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("channel", sa.String(30), nullable=False),
        sa.Column("group_id", sa.String(160), nullable=False),
        sa.Column("class_id", sa.Integer(), sa.ForeignKey("classes.id"), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("channel", "group_id", name="uq_channel_group"),
    )
    op.create_table("documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("logical_key", sa.String(160), nullable=False),
        sa.Column("source_name", sa.String(255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("checksum", sa.String(64)),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("logical_key", "version", name="uq_document_version"),
    )
    op.create_table("document_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("class_scope", sa.String(60), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("page_or_section", sa.String(120)),
        sa.Column("embedding", Vector()),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_document_chunk"),
    )
    op.create_table("events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("channel", sa.String(30), nullable=False),
        sa.Column("external_event_id", sa.String(160), nullable=False),
        sa.Column("group_id", sa.String(160), nullable=False),
        sa.Column("class_id", sa.Integer(), sa.ForeignKey("classes.id"), nullable=False),
        sa.Column("sender_id", sa.String(160), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("channel", "external_event_id", name="uq_event_external"),
    )
    op.create_table("answers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id"), unique=True, nullable=False),
        sa.Column("decision", sa.String(30), nullable=False),
        sa.Column("reply_text", sa.Text(), nullable=False),
        sa.Column("source_chunks", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table("tickets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id"), unique=True, nullable=False),
        sa.Column("class_id", sa.Integer(), sa.ForeignKey("classes.id"), nullable=False),
        sa.Column("reason", sa.String(120), nullable=False),
        sa.Column("status", sa.String(25), nullable=False, server_default="OPEN"),
        sa.Column("resolution", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table("model_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id")),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), server_default="0"),
        sa.Column("estimated_cost_usd", sa.Numeric(12, 6)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    for name in ("model_usage", "tickets", "answers", "events", "document_chunks", "documents", "channel_bindings", "classes"):
        op.drop_table(name)
    # Keep extension: other schemas may depend on it.
