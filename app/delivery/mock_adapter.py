"""Idempotent local inbox adapter; it never calls a real channel API."""

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.delivery.contracts import DeliveryLeaseLost, DeliveryReceipt, DeliverySuppressed
from app.models import (
    Answer,
    ChannelBinding,
    Classroom,
    Document,
    DocumentChunk,
    MockDeliveredMessage,
    OutboxMessage,
)
from app.retrieval.service import ScopeAuthorizationError, resolve_authorized_scope


class MockReplyAdapter:
    def __init__(self, sessions):
        self.sessions = sessions

    def send(self, message):
        with self.sessions.begin() as session:
            outbox = session.get(OutboxMessage, message.outbox_id, with_for_update=True)
            if not (
                outbox
                and outbox.status == "SENDING"
                and outbox.lease_token == message.lease_token
                and outbox.lease_expires_at is not None
                and outbox.lease_expires_at > session.scalar(select(func.now()))
            ):
                raise DeliveryLeaseLost()
            existing = session.scalar(
                select(MockDeliveredMessage).where(
                    MockDeliveredMessage.message_key == message.message_key
                )
            )
            if existing is not None:
                return DeliveryReceipt(f"mock:{existing.id}", True)

            self._validate_scope_and_sources(session, message)
            inserted_id = session.scalar(
                insert(MockDeliveredMessage)
                .values(
                    outbox_id=message.outbox_id,
                    message_key=message.message_key,
                    channel=message.channel,
                    group_id=message.group_id,
                    class_id=message.class_id,
                    body=message.body,
                    citations=message.citations,
                )
                .on_conflict_do_nothing(index_elements=[MockDeliveredMessage.message_key])
                .returning(MockDeliveredMessage.id)
            )
            if inserted_id is None:
                existing = session.scalar(
                    select(MockDeliveredMessage).where(
                        MockDeliveredMessage.message_key == message.message_key
                    )
                )
                return DeliveryReceipt(f"mock:{existing.id}", True)
            return DeliveryReceipt(f"mock:{inserted_id}", False)

    @staticmethod
    def _validate_scope_and_sources(session, message):
        try:
            scope = resolve_authorized_scope(
                session, channel=message.channel, group_id=message.group_id
            )
        except ScopeAuthorizationError:
            raise DeliverySuppressed() from None
        if scope.class_id != message.class_id:
            raise DeliverySuppressed()

        session.execute(
            select(Classroom.id).where(Classroom.id == scope.class_id).with_for_update(read=True)
        ).all()
        session.execute(
            select(ChannelBinding.id)
            .where(ChannelBinding.id == scope.binding_id)
            .with_for_update(read=True)
        ).all()
        try:
            current_scope = resolve_authorized_scope(
                session, channel=message.channel, group_id=message.group_id
            )
        except ScopeAuthorizationError:
            raise DeliverySuppressed() from None
        if (current_scope.binding_id, current_scope.class_id) != (scope.binding_id, scope.class_id):
            raise DeliverySuppressed()

        if message.kind != "ANSWER_ACK":
            return
        outbox = session.get(OutboxMessage, message.outbox_id)
        if outbox is None:
            raise DeliverySuppressed()
        answer = session.scalar(select(Answer).where(Answer.event_id == outbox.event_id))
        if answer is None or answer.source_chunks != message.citations:
            raise DeliverySuppressed()

        for citation in sorted(
            message.citations,
            key=lambda item: (item["doc_id"], item["document_version"], item["chunk_id"]),
        ):
            row = session.execute(
                select(DocumentChunk, Document)
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(
                    Document.logical_key == citation["doc_id"],
                    Document.version == citation["document_version"],
                    Document.active.is_(True),
                    Document.index_status == "READY",
                    Document.review_status != "pending",
                    DocumentChunk.chunk_id == citation["chunk_id"],
                    DocumentChunk.section_id == citation["section_id"],
                    DocumentChunk.source_name == citation["source_name"],
                    DocumentChunk.locator == citation["locator"],
                    DocumentChunk.class_scope == citation["class_scope"],
                    DocumentChunk.class_scope.in_((scope.class_code, "COMMON")),
                    DocumentChunk.review_status != "pending",
                )
                .with_for_update(read=True, of=(Document, DocumentChunk))
            ).first()
            if row is None:
                raise DeliverySuppressed()
