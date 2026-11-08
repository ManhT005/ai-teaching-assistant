from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from sqlalchemy import func, select

from app.events.processor import EventProcessor
from app.events.queue import EventQueue
from app.generation.contracts import ReasonCode
from app.models import Answer, Event, ModelUsage, Ticket


def add_pending(sessions, scope):
    with sessions.begin() as session:
        event = Event(
            channel=scope.channel,
            external_event_id=uuid4().hex,
            group_id=scope.group_id,
            class_id=scope.class_id,
            sender_id="queue-test",
            body="/hoi question",
        )
        session.add(event)
        session.flush()
        return event.id


def test_one_event_has_one_live_claim_and_reclaim_fences_old_token(generation_system):
    sessions, contexts, _, pipeline = generation_system
    event_id = add_pending(sessions, contexts["AUTO-N8N-01"])
    queue = EventQueue(sessions, lease_seconds=120)
    barrier = Barrier(2)

    def claim(worker_id):
        barrier.wait(timeout=10)
        return queue.claim_event(event_id, worker_id=worker_id)

    with ThreadPoolExecutor(max_workers=2) as workers:
        claims = list(workers.map(claim, ("worker-a", "worker-b")))

    current = [claim for claim in claims if claim is not None]
    assert len(current) == 1
    old = current[0]
    with sessions.begin() as session:
        event = session.get(Event, event_id)
        event.lease_expires_at = event.lease_expires_at.replace(year=2020)

    replacement = queue.claim_event(event_id, worker_id="worker-c")
    assert replacement is not None
    assert replacement.lease_token != old.lease_token
    assert replacement.attempts == 2
    stale_result = EventProcessor(sessions, pipeline)._failure(
        old, ReasonCode.PROCESSING_FAILURE, retryable=True
    )
    assert stale_result.status == "PROCESSING"
    with sessions() as session:
        event = session.get(Event, event_id)
        assert event.worker_id == "worker-c"
        assert event.status == "PROCESSING"
        assert session.scalar(select(func.count()).select_from(Answer)) == 0
        assert session.scalar(select(func.count()).select_from(Ticket)) == 0
        assert session.scalar(select(func.count()).select_from(ModelUsage)) == 0


def test_ten_competing_claimers_only_acquire_available_events(generation_system):
    sessions, contexts, _, _ = generation_system
    event_id = add_pending(sessions, contexts["AUTO-N8N-01"])
    barrier = Barrier(10)

    def claim(_):
        barrier.wait(timeout=10)
        return EventQueue(sessions).claim_due(limit=1)

    with ThreadPoolExecutor(max_workers=10) as workers:
        claims = list(workers.map(claim, range(10)))
    owners = [claim[0] for claim in claims if claim]
    assert len(owners) == 1
    assert owners[0].event_id == event_id


def test_expired_lease_at_attempt_limit_is_terminalized(generation_system):
    sessions, contexts, _, _ = generation_system
    event_id = add_pending(sessions, contexts["AUTO-N8N-01"])
    queue = EventQueue(sessions, max_attempts=1)
    claim = queue.claim_event(event_id)
    assert claim is not None
    with sessions.begin() as session:
        event = session.get(Event, event_id)
        event.lease_expires_at = event.lease_expires_at.replace(year=2020)

    assert queue.claim_event(event_id) is None
    with sessions() as session:
        event = session.get(Event, event_id)
        assert event.status == "FAILED_FINAL"
        assert event.last_error_code == "RETRIES_EXHAUSTED"
        assert event.lease_token is None
