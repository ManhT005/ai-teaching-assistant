"""Retain representation-specific source names and human review decisions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004_ingestion_provenance"
down_revision = "0003_rag_ingestion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents", sa.Column("review_audit", JSONB(), nullable=False, server_default="[]")
    )
    op.add_column("document_chunks", sa.Column("source_name", sa.String(255)))
    op.execute("""
        UPDATE document_chunks AS c SET source_name = d.source_name
        FROM documents AS d WHERE c.document_id = d.id
    """)


def downgrade() -> None:
    op.execute("UPDATE documents SET active = false")
    op.drop_column("document_chunks", "source_name")
    op.drop_column("documents", "review_audit")
