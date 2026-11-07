"""Add generated lexical search data without changing source content or vectors."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TSVECTOR

revision = "0005_hybrid_retrieval"
down_revision = "0004_ingestion_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_chunks",
        sa.Column(
            "search_vector",
            TSVECTOR(),
            sa.Computed("to_tsvector('simple'::regconfig, coalesce(content, ''))", persisted=True),
        ),
    )
    op.create_index(
        "ix_document_chunks_search_vector_gin",
        "document_chunks",
        ["search_vector"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_document_chunks_search_vector_gin", table_name="document_chunks")
    op.drop_column("document_chunks", "search_vector")
