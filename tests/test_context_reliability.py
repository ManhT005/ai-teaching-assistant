from uuid import uuid4

from sqlalchemy import select

from app.events.processor import EventProcessor
from app.models import Event, EventContextResolution, OutboxMessage


def _add_event(sessions, scope, question):
    with sessions.begin() as session:
        event = Event(
            channel=scope.channel,
            group_id=scope.group_id,
            class_id=scope.class_id,
            external_event_id=uuid4().hex,
            sender_id="context-student",
            body="/hoi " + question,
        )
        session.add(event)
        session.flush()
        return event.id


def test_followup_uses_sent_same_sender_history_but_fresh_evidence(generation_system):
    sessions, contexts, _, pipeline = generation_system
    scope = contexts["AUTO-N8N-01"]
    processor = EventProcessor(sessions, pipeline)

    first_id = _add_event(sessions, scope, "n8n HTTP 401 Unauthorized")
    first = processor.process_event(first_id)
    assert first.status == "ANSWERED"

    with sessions.begin() as session:
        outbox = session.scalar(select(OutboxMessage).where(OutboxMessage.event_id == first_id))
        outbox.status = "SENT"

    second_id = _add_event(sessions, scope, "Còn 403?")
    second = processor.process_event(second_id)

    assert second.status == "HANDOFF"
    assert second.reason_code == "INCOMPLETE_EVIDENCE"
    with sessions() as session:
        snapshot = session.get(EventContextResolution, second_id)
        assert snapshot.resolution_status == "RESOLVED"
        assert snapshot.prior_event_ids == [first_id]
        assert "403" in snapshot.resolved_query
        assert "401" not in snapshot.resolved_query


def test_elliptical_question_without_delivered_history_hands_off(generation_system):
    sessions, contexts, _, pipeline = generation_system
    scope = contexts["AUTO-N8N-01"]
    pipeline.provider.generate = lambda *_: (_ for _ in ()).throw(
        AssertionError("provider should not run")
    )
    event_id = _add_event(sessions, scope, "Cái đó thì sao?")

    result = EventProcessor(sessions, pipeline).process_event(event_id)

    assert result.status == "HANDOFF"
    assert result.reason_code == "AMBIGUOUS_QUESTION"
    with sessions() as session:
        snapshot = session.get(EventContextResolution, event_id)
        assert snapshot.resolution_status == "NO_CONTEXT"
        assert snapshot.prior_event_ids == []


def test_secret_like_query_is_not_copied_to_resolution_snapshot(generation_system):
    sessions, contexts, _, pipeline = generation_system
    scope = contexts["AUTO-N8N-01"]
    event_id = _add_event(sessions, scope, "api_key=test-secret-value-123456")

    result = EventProcessor(sessions, pipeline).process_event(event_id)

    assert result.status == "HANDOFF"
    with sessions() as session:
        snapshot = session.get(EventContextResolution, event_id)
        assert snapshot.resolution_status == "INVALID"
        assert "test-secret-value" not in snapshot.resolved_query
