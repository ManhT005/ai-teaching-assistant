"""Pin vectors and add provenance, review, staging and revocation lifecycle.

Legacy rows are retained but disabled pending a complete reindex/review. Existing
non-384 vectors cause a transactional failure; they are never discarded.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from pgvector.sqlalchemy import Vector


revision = "0003_rag_ingestion"
down_revision = "0002_source_received_at"
branch_labels = None
depends_on = None

MODEL_KEY = "intfloat/multilingual-e5-small@614241f622f53c4eeff9890bdc4f31cfecc418b3"


def upgrade() -> None:
    # The cast checks legacy dimensions before any lifecycle state is changed.
    op.alter_column("document_chunks", "embedding", type_=Vector(384), postgresql_using="embedding::vector(384)")

    document_columns = [
        sa.Column("external_version", sa.String(120)),
        sa.Column("data_origin", sa.String(30), nullable=False, server_default="provided_sample"),
        sa.Column("review_status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("index_status", sa.String(20), nullable=False, server_default="STAGING"),
        sa.Column("embedding_model", sa.String(255), nullable=False, server_default=MODEL_KEY),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False, server_default="384"),
        sa.Column("parser_revision", sa.String(100)),
        sa.Column("index_fingerprint", sa.String(64)),
        sa.Column("expected_chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("indexed_at", sa.DateTime(timezone=True)),
        sa.Column("failure_code", sa.String(80)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revocation_reason", sa.String(255)),
    ]

    for column in document_columns:
        op.add_column("documents", column)

    op.execute("UPDATE documents SET active = false, index_status = 'FAILED', failure_code = 'LEGACY_REINDEX_REQUIRED'")

    chunk_columns = [
        sa.Column("section_id", sa.String(120)),
        sa.Column("section_chunk_index", sa.Integer()),
        sa.Column("chunk_id", sa.String(64)),
        sa.Column("locator", sa.Text()),
        sa.Column("content_hash", sa.String(64)),
        sa.Column("source_checksum", sa.String(64)),
        sa.Column("review_status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("data_origin", sa.String(30), nullable=False, server_default="provided_sample"),
        sa.Column("topics", JSONB(), nullable=False, server_default="[]"),
        sa.Column("token_count", sa.Integer()),
    ]

    for column in chunk_columns:
        op.add_column("document_chunks", column)

    op.create_index("uq_document_active", "documents", ["logical_key"], unique=True, postgresql_where=sa.text("active"))
    checks = {
        "documents": {
            "ck_document_index_status": "index_status IN ('STAGING', 'READY', 'FAILED', 'REVOKED')",
            "ck_document_review": "review_status IN ('pending', 'provided_sample', 'approved_demo')",
            "ck_document_origin": "data_origin IN ('provided_sample', 'synthetic_demo')",
            "ck_document_provenance": "NOT (data_origin = 'synthetic_demo' AND review_status = 'provided_sample')",
            "ck_document_embedding": f"embedding_model = '{MODEL_KEY}' AND embedding_dimension = 384",
            "ck_document_active_ready": "NOT active OR (index_status = 'READY' AND review_status <> 'pending' AND indexed_at IS NOT NULL)",
        },
        "document_chunks": {
            "ck_chunk_scope": "class_scope IN ('GS-KPI-01', 'AUTO-N8N-01', 'COMMON')",
            "ck_chunk_review": "review_status IN ('pending', 'provided_sample', 'approved_demo')",
            "ck_chunk_origin": "data_origin IN ('provided_sample', 'synthetic_demo')",
            "ck_chunk_provenance": "NOT (data_origin = 'synthetic_demo' AND review_status = 'provided_sample')",
        },
    }

    for table, constraints in checks.items():
        for name, expression in constraints.items():
            op.create_check_constraint(name, table, expression)


def downgrade() -> None:
    # A downgrade cannot preserve review/revocation semantics. Disable all sources.
    op.execute("UPDATE documents SET active = false")

    for name in ("scope", "review", "origin", "provenance"):
        op.drop_constraint(f"ck_chunk_{name}", "document_chunks")

    for name in ("index_status", "review", "origin", "provenance", "embedding", "active_ready"):
        op.drop_constraint(f"ck_document_{name}", "documents")

    op.drop_index("uq_document_active", table_name="documents")

    for column in ("section_id", "section_chunk_index", "chunk_id", "locator", "content_hash", "source_checksum", "review_status", "data_origin", "topics", "token_count"):
        op.drop_column("document_chunks", column)

    for column in ("external_version", "data_origin", "review_status", "index_status", "embedding_model", "embedding_dimension", "parser_revision", "index_fingerprint", "expected_chunks", "indexed_at", "failure_code", "revoked_at", "revocation_reason"):
        op.drop_column("documents", column)

    op.alter_column("document_chunks", "embedding", type_=Vector(), postgresql_using="embedding::vector")
