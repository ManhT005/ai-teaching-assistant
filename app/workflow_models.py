"""Staff authorization, human replies and governed FAQ publication records."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class CreatedAt:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StaffActor(CreatedAt, Base):
    __tablename__ = "staff_actors"
    id: Mapped[int] = mapped_column(primary_key=True)
    alias: Mapped[str] = mapped_column(String(100), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")


class StaffClassGrant(Base):
    __tablename__ = "staff_class_grants"
    __table_args__ = (
        UniqueConstraint("actor_id", "class_id", "role"),
        CheckConstraint("role IN ('TEACHER', 'FAQ_REVIEWER', 'OPERATOR')", name="ck_staff_role"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("staff_actors.id"))
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id"))
    role: Mapped[str] = mapped_column(String(20))


class StaffCredential(CreatedAt, Base):
    __tablename__ = "staff_credentials"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("staff_actors.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TicketAction(CreatedAt, Base):
    __tablename__ = "ticket_actions"
    __table_args__ = (UniqueConstraint("ticket_id", "action_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"))
    actor_id: Mapped[int] = mapped_column(ForeignKey("staff_actors.id"))
    action: Mapped[str] = mapped_column(String(40))
    from_state: Mapped[str] = mapped_column(String(25))
    to_state: Mapped[str] = mapped_column(String(25))
    expected_version: Mapped[int] = mapped_column(Integer)
    action_key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))


class TeacherReply(CreatedAt, Base):
    __tablename__ = "teacher_replies"
    __table_args__ = (
        CheckConstraint("status IN ('DRAFT', 'APPROVED', 'SENT')", name="ck_teacher_reply_status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), unique=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    body: Mapped[str] = mapped_column(Text)
    body_hash: Mapped[str] = mapped_column(String(64))
    author_actor_id: Mapped[int] = mapped_column(ForeignKey("staff_actors.id"))
    approver_actor_id: Mapped[int | None] = mapped_column(ForeignKey("staff_actors.id"))
    approval_credential_id: Mapped[int | None] = mapped_column(ForeignKey("staff_credentials.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")


class TeacherReplyOutbox(CreatedAt, Base):
    __tablename__ = "teacher_reply_outbox"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'SENDING', 'RETRY_WAIT', 'SENT', 'DEAD', 'SUPPRESSED')",
            name="ck_teacher_outbox_status",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    reply_id: Mapped[int] = mapped_column(ForeignKey("teacher_replies.id"), unique=True)
    message_key: Mapped[str] = mapped_column(String(160), unique=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id"))
    binding_id: Mapped[int] = mapped_column(ForeignKey("channel_bindings.id"))
    channel: Mapped[str] = mapped_column(String(30))
    group_id: Mapped[str] = mapped_column(String(160))
    approved_revision: Mapped[int] = mapped_column(Integer)
    approved_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TeacherMockReceipt(CreatedAt, Base):
    __tablename__ = "teacher_mock_receipts"
    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_outbox_id: Mapped[int] = mapped_column(
        ForeignKey("teacher_reply_outbox.id"), unique=True
    )
    message_key: Mapped[str] = mapped_column(String(160), unique=True)
    body: Mapped[str] = mapped_column(Text)


class FAQCandidate(CreatedAt, Base):
    __tablename__ = "faq_candidates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT', 'SUBMITTED', 'APPROVED', 'REJECTED', 'INDEXING', 'FAILED', 'PUBLISHED', 'REVOKED')",
            name="ck_faq_status",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"))
    reply_id: Mapped[int] = mapped_column(ForeignKey("teacher_replies.id"))
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id"))
    author_actor_id: Mapped[int] = mapped_column(ForeignKey("staff_actors.id"))
    creation_key: Mapped[str] = mapped_column(String(100), unique=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")


class FAQRevision(CreatedAt, Base):
    __tablename__ = "faq_revisions"
    __table_args__ = (UniqueConstraint("faq_id", "revision"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    faq_id: Mapped[int] = mapped_column(ForeignKey("faq_candidates.id"))
    revision: Mapped[int] = mapped_column(Integer)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    topics: Mapped[list] = mapped_column(JSONB, default=list)
    source_references: Mapped[list] = mapped_column(JSONB, default=list)
    body_hash: Mapped[str] = mapped_column(String(64))
    reviewer_actor_id: Mapped[int | None] = mapped_column(ForeignKey("staff_actors.id"))
    reviewer_credential_id: Mapped[int | None] = mapped_column(ForeignKey("staff_credentials.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_reason: Mapped[str | None] = mapped_column(String(500))
    decision: Mapped[str | None] = mapped_column(String(20))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FAQPublication(CreatedAt, Base):
    __tablename__ = "faq_publications"
    __table_args__ = (UniqueConstraint("faq_id", "revision"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    faq_id: Mapped[int] = mapped_column(ForeignKey("faq_candidates.id"))
    revision: Mapped[int] = mapped_column(Integer)
    logical_doc_id: Mapped[str] = mapped_column(String(160))
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))
    artifact_checksum: Mapped[str | None] = mapped_column(String(64))
    manifest_checksum: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="INDEXING")
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
