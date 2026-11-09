"""Resolve a small, currently eligible same-sender context window."""

import hashlib
import re
from datetime import timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.context.classifier import classify
from app.context.contracts import (
    ContextTurn,
    QueryResolution,
    ResolutionReason,
    ResolutionStatus,
    TeachingIntent,
)
from app.context.rewriter import resolve_query
from app.generation.safety import is_sensitive_policy, is_unsafe
from app.models import Answer, Document, DocumentChunk, Event, EventContextResolution, OutboxMessage
from app.retrieval.service import (
    AuthorizedScope,
    eligible_chunk_predicates,
    validate_authorized_scope,
)

_SECRET = re.compile(r"(?i)(?:api[_-]?key|token|password|secret)\s*[:=]\s*\S+")
REVISION = "context-rewrite-v1"


def _safe_question(text: str) -> str | None:
    value = " ".join(_SECRET.sub("[redacted]", text).split())[:700]
    if is_unsafe(value) or is_sensitive_policy(value) or not value:
        return None
    return value


class ContextResolver:
    def __init__(self, sessions, *, ttl_minutes=20, max_turns=5, max_characters=3500):
        self.sessions = sessions
        self.ttl = timedelta(minutes=ttl_minutes)
        self.max_turns = min(max_turns, 5)
        self.max_characters = max_characters

    def resolve(self, event_id: int, question: str, scope: AuthorizedScope) -> QueryResolution:
        with self.sessions.begin() as session:
            event = session.get(Event, event_id)
            if event is None:
                raise ValueError("Context requires a stored event")
            validate_authorized_scope(session, scope)
            normalized = " ".join(question.split())
            redacted = _SECRET.sub("[redacted]", normalized)
            unsafe_input = redacted != normalized or is_unsafe(normalized)
            stored_question = "[redacted sensitive input]" if unsafe_input else redacted[:700]
            query_hash = hashlib.sha256(stored_question.encode("utf-8")).hexdigest()
            existing = session.get(EventContextResolution, event_id)
            if existing is not None:
                if existing.original_query_hash != query_hash:
                    return QueryResolution(
                        event_id,
                        ResolutionStatus.INVALID,
                        TeachingIntent.STANDALONE,
                        stored_question,
                        (),
                        ResolutionReason.AMBIGUOUS_REFERENT,
                        REVISION,
                    )
                if not self.snapshot_is_current(session, event_id, scope):
                    return QueryResolution(
                        event_id,
                        ResolutionStatus.INVALID,
                        TeachingIntent.STANDALONE,
                        stored_question,
                        (),
                        ResolutionReason.AMBIGUOUS_REFERENT,
                        REVISION,
                    )
                return QueryResolution(
                    event_id,
                    ResolutionStatus(existing.resolution_status),
                    TeachingIntent(existing.intent),
                    existing.resolved_query,
                    tuple(existing.prior_event_ids),
                    ResolutionReason.UNIQUE_RECENT_REFERENT
                    if existing.resolution_status == "RESOLVED"
                    else ResolutionReason.NO_ELIGIBLE_HISTORY,
                    existing.rewriter_revision,
                )

            intent = classify(stored_question)
            if unsafe_input or len(normalized) > 700:
                resolution = QueryResolution(
                    event_id,
                    ResolutionStatus.INVALID,
                    intent,
                    stored_question,
                    (),
                    ResolutionReason.AMBIGUOUS_REFERENT,
                    REVISION,
                )
            else:
                turns = (
                    self._eligible_turns(session, event, scope)
                    if intent not in {TeachingIntent.STANDALONE, TeachingIntent.NEW_TOPIC}
                    else ()
                )
                resolution = resolve_query(event_id, normalized, turns)
            if len(resolution.standalone_question) > self.max_characters:
                resolution = QueryResolution(
                    event_id,
                    ResolutionStatus.INVALID,
                    intent,
                    stored_question,
                    (),
                    ResolutionReason.AMBIGUOUS_REFERENT,
                    REVISION,
                )
            session.execute(
                insert(EventContextResolution)
                .values(
                    event_id=event_id,
                    prior_event_ids=list(resolution.antecedent_event_ids),
                    resolution_status=resolution.status.value,
                    intent=resolution.intent.value,
                    resolved_query=resolution.standalone_question,
                    original_query_hash=query_hash,
                    rewriter_revision=resolution.revision,
                )
                .on_conflict_do_nothing(index_elements=[EventContextResolution.event_id])
            )
            session.flush()
            saved = session.get(EventContextResolution, event_id)
            return QueryResolution(
                event_id,
                ResolutionStatus(saved.resolution_status),
                TeachingIntent(saved.intent),
                saved.resolved_query,
                tuple(saved.prior_event_ids),
                resolution.reason,
                saved.rewriter_revision,
            )

    def _eligible_turns(
        self, session, current: Event, scope: AuthorizedScope
    ) -> tuple[ContextTurn, ...]:
        lower_bound = current.received_at - self.ttl
        prior_order = or_(
            Event.received_at < current.received_at,
            and_(Event.received_at == current.received_at, Event.id < current.id),
        )
        rows = session.execute(
            select(Event, Answer)
            .join(Answer, Answer.event_id == Event.id)
            .join(OutboxMessage, OutboxMessage.event_id == Event.id)
            .where(
                prior_order,
                Event.received_at >= lower_bound,
                Event.status == "ANSWERED",
                Event.channel == current.channel,
                Event.group_id == current.group_id,
                Event.class_id == current.class_id,
                Event.sender_id == current.sender_id,
                OutboxMessage.status == "SENT",
                OutboxMessage.kind == "ANSWER_ACK",
            )
            .order_by(Event.received_at.desc(), Event.id.desc())
            .limit(50)
        ).all()
        eligible = []
        characters = 0
        for prior, answer in rows:
            validation = answer.validation_summary or {}
            if (
                answer.decision != "ANSWER"
                or validation.get("reason_code") != "GROUNDED_ANSWER"
                or not self._sources_current(session, answer.source_chunks, scope)
            ):
                continue
            question = _safe_question(prior.body)
            if question is None:
                continue
            if characters + len(question) > self.max_characters:
                continue
            eligible.append(ContextTurn(prior.id, question))
            characters += len(question)
            if len(eligible) == self.max_turns:
                break
        return tuple(reversed(eligible))

    @classmethod
    def snapshot_is_current(cls, session, event_id: int, scope: AuthorizedScope) -> bool:
        """Recheck referenced turns before finalizing a query resolved with history."""
        snapshot = session.get(EventContextResolution, event_id)
        if snapshot is None or not snapshot.prior_event_ids:
            return True
        current = session.get(Event, event_id)
        if current is None:
            return False
        validate_authorized_scope(session, scope)
        for prior_id in snapshot.prior_event_ids:
            row = session.execute(
                select(Event, Answer)
                .join(Answer, Answer.event_id == Event.id)
                .join(OutboxMessage, OutboxMessage.event_id == Event.id)
                .where(
                    Event.id == prior_id,
                    Event.status == "ANSWERED",
                    Event.channel == current.channel,
                    Event.group_id == current.group_id,
                    Event.class_id == current.class_id,
                    Event.sender_id == current.sender_id,
                    OutboxMessage.status == "SENT",
                    OutboxMessage.kind == "ANSWER_ACK",
                )
            ).one_or_none()
            if (
                row is None
                or (row.Answer.validation_summary or {}).get("reason_code") != "GROUNDED_ANSWER"
                or not cls._sources_current(session, row.Answer.source_chunks, scope)
            ):
                return False
        return True

    @staticmethod
    def _sources_current(session, citations, scope) -> bool:
        if not citations:
            return False
        for citation in citations:
            row = session.execute(
                select(DocumentChunk.id)
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(
                    DocumentChunk.chunk_id == citation.get("chunk_id"),
                    Document.logical_key == citation.get("doc_id"),
                    Document.version == citation.get("document_version"),
                    DocumentChunk.section_id == citation.get("section_id"),
                    DocumentChunk.source_name == citation.get("source_name"),
                    DocumentChunk.locator == citation.get("locator"),
                    DocumentChunk.class_scope == citation.get("class_scope"),
                    *eligible_chunk_predicates(scope.class_code),
                )
            ).first()
            if row is None:
                return False
        return True
