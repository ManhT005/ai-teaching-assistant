from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.context.resolver import ContextResolver
from app.events.processor import EventProcessor
from app.llm.provider import ProviderFailure
from app.models import ChannelBinding, Document, Event, EventContextResolution, OutboxMessage
from app.retrieval.service import ScopeAuthorizationError
from test_context_reliability import _add_event


def delivered_turn(system):
    sessions, contexts, _, pipeline = system
    scope = contexts["AUTO-N8N-01"]
    event_id = _add_event(sessions, scope, "n8n HTTP 401 Unauthorized")
    assert EventProcessor(sessions, pipeline).process_event(event_id).status == "ANSWERED"
    with sessions.begin() as session:
        session.scalar(
            select(OutboxMessage).where(OutboxMessage.event_id == event_id)
        ).status = "SENT"
    return event_id


@pytest.mark.parametrize(
    "boundary", ["sender", "class", "group", "channel", "expired", "revoked", "unsent"]
)
def test_ineligible_history_is_never_used(generation_system, boundary):
    sessions, contexts, _, pipeline = generation_system
    prior_id = delivered_turn(generation_system)
    scope = contexts["AUTO-N8N-01"]
    if boundary == "class":
        scope = contexts["GS-KPI-01"]
    current_id = _add_event(sessions, scope, "Còn 403?")
    with sessions.begin() as session:
        prior = session.get(Event, prior_id)
        current = session.get(Event, current_id)
        if boundary == "sender":
            current.sender_id = "different-student"
        elif boundary in {"group", "channel"}:
            setattr(prior, "group_id" if boundary == "group" else "channel", "different")
        elif boundary == "expired":
            prior.received_at = current.received_at - timedelta(minutes=20, seconds=1)
        elif boundary == "revoked":
            session.scalar(select(Document).where(Document.logical_key == "AUTO")).active = False
        elif boundary == "unsent":
            session.scalar(
                select(OutboxMessage).where(OutboxMessage.event_id == prior_id)
            ).status = "PENDING"
    result = EventProcessor(sessions, pipeline).process_event(current_id)
    assert result.status == "HANDOFF"
    assert result.reason_code == "AMBIGUOUS_QUESTION"
    with sessions() as session:
        snapshot = session.get(EventContextResolution, current_id)
        assert snapshot.prior_event_ids == []
        assert snapshot.resolution_status == "NO_CONTEXT"


def test_resolver_rejects_scope_for_another_event(generation_system):
    sessions, contexts, _, _ = generation_system
    event_id = _add_event(sessions, contexts["AUTO-N8N-01"], "Còn 403?")
    with pytest.raises(ScopeAuthorizationError):
        ContextResolver(sessions).resolve(event_id, "Còn 403?", contexts["GS-KPI-01"])


def test_snapshot_retry_remains_frozen_and_revocation_blocks_reuse(generation_system):
    sessions, contexts, _, _ = generation_system
    prior_id = delivered_turn(generation_system)
    scope = contexts["AUTO-N8N-01"]
    event_id = _add_event(sessions, scope, "Còn 403?")
    resolver = ContextResolver(sessions)
    first = resolver.resolve(event_id, "Còn 403?", scope)
    assert first.antecedent_event_ids == (prior_id,)
    with sessions.begin() as session:
        session.get(Event, prior_id).body = "/hoi SUMIFS"
    replay = resolver.resolve(event_id, "Còn 403?", scope)
    assert replay.standalone_question == first.standalone_question
    with sessions.begin() as session:
        session.scalar(select(Document).where(Document.logical_key == "AUTO")).active = False
    assert resolver.resolve(event_id, "Còn 403?", scope).status == "INVALID"


def test_provider_retry_revalidates_context_before_second_call(generation_system, monkeypatch):
    sessions, contexts, _, pipeline = generation_system
    delivered_turn(generation_system)
    scope = contexts["AUTO-N8N-01"]
    event_id = _add_event(sessions, scope, "Còn 403?")
    processor = EventProcessor(sessions, pipeline)

    def unavailable(*_):
        raise ProviderFailure("temporary", retryable=True)

    monkeypatch.setattr(pipeline, "run", unavailable)
    assert processor.process_event(event_id).status == "FAILED_RETRYABLE"
    with sessions.begin() as session:
        frozen = session.get(EventContextResolution, event_id).resolved_query
        session.scalar(select(Document).where(Document.logical_key == "AUTO")).active = False
        session.get(Event, event_id).next_attempt_at = session.scalar(
            select(func.now())
        ) - timedelta(seconds=1)
    monkeypatch.setattr(
        pipeline, "run", lambda *_: pytest.fail("revoked context reached generation")
    )
    result = processor.process_event(event_id)
    assert result.status == "HANDOFF"
    with sessions() as session:
        assert session.get(EventContextResolution, event_id).resolved_query == frozen
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutboxMessage)
                .where(OutboxMessage.event_id == event_id)
            )
            == 1
        )


def test_binding_revoked_before_snapshot_retry(generation_system):
    sessions, contexts, _, _ = generation_system
    delivered_turn(generation_system)
    scope = contexts["AUTO-N8N-01"]
    event_id = _add_event(sessions, scope, "Còn 403?")
    resolver = ContextResolver(sessions)
    resolver.resolve(event_id, "Còn 403?", scope)
    with sessions.begin() as session:
        session.get(ChannelBinding, scope.binding_id).active = False
    with pytest.raises(ScopeAuthorizationError):
        resolver.resolve(event_id, "Còn 403?", scope)
