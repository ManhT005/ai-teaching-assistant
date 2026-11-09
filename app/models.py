"""One PostgreSQL database for events and versioned, reviewed RAG vectors."""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.knowledge.embedding_contract import DIMENSION, MODEL_KEY


class Base(DeclarativeBase):
    pass


class Classroom(Base):
    __tablename__ = "classes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    bot_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )


class ChannelBinding(Base):
    __tablename__ = "channel_bindings"
    __table_args__ = (UniqueConstraint("channel", "group_id", name="uq_channel_group"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    group_id: Mapped[str] = mapped_column(String(160), nullable=False)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id"), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    classroom: Mapped[Classroom] = relationship()


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("logical_key", "version", name="uq_document_version"),
        Index("uq_document_active", "logical_key", unique=True, postgresql_where=text("active")),
        CheckConstraint(
            "index_status IN ('STAGING', 'READY', 'FAILED', 'REVOKED')",
            name="ck_document_index_status",
        ),
        CheckConstraint(
            "review_status IN ('pending', 'provided_sample', 'approved_demo')",
            name="ck_document_review",
        ),
        CheckConstraint(
            "data_origin IN ('provided_sample', 'synthetic_demo')", name="ck_document_origin"
        ),
        CheckConstraint(
            "NOT (data_origin = 'synthetic_demo' AND review_status = 'provided_sample')",
            name="ck_document_provenance",
        ),
        CheckConstraint(
            f"embedding_model = '{MODEL_KEY}' AND embedding_dimension = {DIMENSION}",
            name="ck_document_embedding",
        ),
        CheckConstraint(
            "NOT active OR (index_status = 'READY' AND review_status <> 'pending' AND indexed_at IS NOT NULL)",
            name="ck_document_active_ready",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    logical_key: Mapped[str] = mapped_column(String(160), nullable=False)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    checksum: Mapped[str | None] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    external_version: Mapped[str | None] = mapped_column(String(120))
    data_origin: Mapped[str] = mapped_column(
        String(30), nullable=False, default="provided_sample", server_default="provided_sample"
    )
    review_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending", server_default="pending"
    )
    index_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="STAGING", server_default="STAGING"
    )
    embedding_model: Mapped[str] = mapped_column(
        String(255), nullable=False, default=MODEL_KEY, server_default=MODEL_KEY
    )
    embedding_dimension: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DIMENSION, server_default=str(DIMENSION)
    )
    parser_revision: Mapped[str | None] = mapped_column(String(100))
    index_fingerprint: Mapped[str | None] = mapped_column(String(64))
    expected_chunks: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(80))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(String(255))
    review_audit: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_document_chunk"),
        Index("ix_document_chunks_search_vector_gin", "search_vector", postgresql_using="gin"),
        CheckConstraint(
            "class_scope IN ('GS-KPI-01', 'AUTO-N8N-01', 'COMMON')", name="ck_chunk_scope"
        ),
        CheckConstraint(
            "review_status IN ('pending', 'provided_sample', 'approved_demo')",
            name="ck_chunk_review",
        ),
        CheckConstraint(
            "data_origin IN ('provided_sample', 'synthetic_demo')", name="ck_chunk_origin"
        ),
        CheckConstraint(
            "NOT (data_origin = 'synthetic_demo' AND review_status = 'provided_sample')",
            name="ck_chunk_provenance",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    # Scope is assigned per chunk: GS-KPI-01 / AUTO-N8N-01 / COMMON.
    class_scope: Mapped[str] = mapped_column(String(60), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    page_or_section: Mapped[str | None] = mapped_column(String(120))
    section_id: Mapped[str | None] = mapped_column(String(120))
    source_name: Mapped[str | None] = mapped_column(String(255))
    section_chunk_index: Mapped[int | None] = mapped_column(Integer)
    chunk_id: Mapped[str | None] = mapped_column(String(64))
    locator: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    source_checksum: Mapped[str | None] = mapped_column(String(64))
    review_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending", server_default="pending"
    )
    data_origin: Mapped[str] = mapped_column(
        String(30), nullable=False, default="provided_sample", server_default="provided_sample"
    )
    topics: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    token_count: Mapped[int | None] = mapped_column(Integer)
    embedding = mapped_column(Vector(DIMENSION), nullable=True)
    search_vector = mapped_column(
        TSVECTOR(),
        Computed("to_tsvector('simple'::regconfig, coalesce(content, ''))", persisted=True),
    )


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("channel", "external_event_id", name="uq_event_external"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(160), nullable=False)
    group_id: Mapped[str] = mapped_column(String(160), nullable=False)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id"), nullable=False)
    sender_id: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDING", server_default="PENDING"
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(String(120))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_category: Mapped[str | None] = mapped_column(String(60))
    # Client timestamp is preserved separately from the trusted server intake time.
    source_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EventContextResolution(Base):
    __tablename__ = "event_context_resolutions"
    __table_args__ = (
        CheckConstraint("jsonb_array_length(prior_event_ids) <= 5", name="ck_context_prior_count"),
        CheckConstraint(
            "resolution_status IN ('STANDALONE', 'RESOLVED', 'AMBIGUOUS', 'NO_CONTEXT', 'INVALID')",
            name="ck_context_status",
        ),
        CheckConstraint("length(resolved_query) <= 700", name="ck_context_query_length"),
    )
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    prior_event_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    resolution_status: Mapped[str] = mapped_column(String(20), nullable=False)
    intent: Mapped[str] = mapped_column(String(30), nullable=False)
    resolved_query: Mapped[str] = mapped_column(String(700), nullable=False)
    original_query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    rewriter_revision: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Answer(Base):
    __tablename__ = "answers"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), unique=True, nullable=False)
    decision: Mapped[str] = mapped_column(String(30), nullable=False)
    reply_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_chunks: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    validation_summary: Mapped[dict | None] = mapped_column(JSONB)
    provider_request_id: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Ticket(Base):
    __tablename__ = "tickets"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), unique=True, nullable=False)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id"), nullable=False)
    reason: Mapped[str] = mapped_column(String(120), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(80))
    assigned_to: Mapped[str | None] = mapped_column(String(160))
    evidence_summary: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(
        String(25), nullable=False, default="OPEN", server_default="OPEN"
    )
    resolution: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModelUsage(Base):
    __tablename__ = "model_usage"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"))
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    request_id: Mapped[str | None] = mapped_column(String(255))
    estimated_cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OutboxMessage(Base):
    __tablename__ = "outbox_messages"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_outbox_event"),
        UniqueConstraint("message_key", name="uq_outbox_message_key"),
        CheckConstraint(
            "status IN ('PENDING', 'SENDING', 'RETRY_WAIT', 'SENT', 'DEAD', 'SUPPRESSED')",
            name="ck_outbox_status",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    message_key: Mapped[str] = mapped_column(String(160), nullable=False)
    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    group_id: Mapped[str] = mapped_column(String(160), nullable=False)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_message_id: Mapped[str | None] = mapped_column(String(160))
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MockDeliveredMessage(Base):
    __tablename__ = "mock_delivered_messages"
    __table_args__ = (
        UniqueConstraint("outbox_id", name="uq_mock_delivery_outbox"),
        UniqueConstraint("message_key", name="uq_mock_delivery_message_key"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    outbox_id: Mapped[int] = mapped_column(ForeignKey("outbox_messages.id"), nullable=False)
    message_key: Mapped[str] = mapped_column(String(160), nullable=False)
    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    group_id: Mapped[str] = mapped_column(String(160), nullable=False)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    delivered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RateLimitWindow(Base):
    __tablename__ = "rate_limit_windows"
    __table_args__ = (
        UniqueConstraint("scope_type", "scope_key", "window_start", name="uq_rate_window"),
        CheckConstraint("scope_type IN ('sender', 'group')", name="ck_rate_scope_type"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(64), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
