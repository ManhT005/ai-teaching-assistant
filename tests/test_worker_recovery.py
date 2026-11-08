from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from app.events.processor import EventProcessor
from app.events.retry import RetryPolicy
from app.events.worker import EventWorker
from app.llm.provider import ProviderFailure
from app.models import Event


def test_worker_retries_due_failure_and_finishes_at_attempt_limit(generation_system):
    sessions, contexts, _, pipeline = generation_system
    scope = contexts["AUTO-N8N-01"]
    with sessions.begin() as session:
        event = Event(
            channel=scope.channel,
            external_event_id=uuid4().hex,
            group_id=scope.group_id,
            class_id=scope.class_id,
            sender_id="recovery-test",
            body="/hoi n8n 401 Unauthorized",
        )
        session.add(event)
        session.flush()
        event_id = event.id

    class FailingProvider:
        def generate(self, *_):
            raise ProviderFailure(retryable=True)

    pipeline.provider = FailingProvider()
    processor = EventProcessor(sessions, pipeline)
    processor.queue.max_attempts = 2
    processor.retry_policy = RetryPolicy(base_seconds=1, cap_seconds=1, jitter=lambda _: 0)
    worker = EventWorker(processor.queue, processor)

    first = worker.run_once(limit=1, worker_id="recovery-test")[0]
    assert first.status == "FAILED_RETRYABLE"
    with sessions.begin() as session:
        event = session.get(Event, event_id)
        assert event.attempts == 1
        assert event.next_attempt_at > datetime.now(UTC)
        event.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)

    second = worker.run_once(limit=1, worker_id="recovery-test")[0]
    assert second.status == "FAILED_FINAL"
    assert second.reason_code == "RETRIES_EXHAUSTED"
    with sessions() as session:
        event = session.scalar(select(Event).where(Event.id == event_id))
        assert event.attempts == 2
        assert event.finished_at is not None
        assert event.lease_token is None


def test_worker_reclaims_expired_processing_event(generation_system):
    sessions, contexts, _, pipeline = generation_system
    scope = contexts["AUTO-N8N-01"]
    with sessions.begin() as session:
        event = Event(
            channel=scope.channel,
            external_event_id=uuid4().hex,
            group_id=scope.group_id,
            class_id=scope.class_id,
            sender_id="recovery-test",
            body="/hoi n8n 401 Unauthorized",
        )
        session.add(event)
        session.flush()
        event_id = event.id
    processor = EventProcessor(sessions, pipeline)
    original = processor.queue.claim_event(event_id, worker_id="crashed-worker")
    assert original is not None
    with sessions.begin() as session:
        event = session.get(Event, event_id)
        event.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    result = EventWorker(processor.queue, processor).run_once(limit=1, worker_id="recovery-worker")[
        0
    ]
    assert result.status == "ANSWERED"
    with sessions() as session:
        event = session.get(Event, event_id)
        assert event.attempts == 2
        assert event.worker_id is None
