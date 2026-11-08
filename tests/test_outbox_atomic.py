from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import func, select

import app.events.processor as processor_module
from app.delivery.contracts import DeliveryFailure, DeliveryLeaseLost
from app.delivery.dispatcher import DeliveryWorker
from app.delivery.mock_adapter import MockReplyAdapter
from app.events.processor import EventProcessor
from app.events.queue import utc_now
from app.models import (
    Answer,
    ChannelBinding,
    Classroom,
    Event,
    MockDeliveredMessage,
    OutboxMessage,
    Ticket,
)


def add_event(sessions, scope, question="n8n 401 Unauthorized"):
    with sessions.begin() as session:
        event = Event(
            channel=scope.channel,
            external_event_id=uuid4().hex,
            group_id=scope.group_id,
            class_id=scope.class_id,
            sender_id="outbox-test",
            body=f"/hoi {question}",
        )
        session.add(event)
        session.flush()
        return event.id


def test_answer_and_outbox_are_created_once_in_same_processing_outcome(generation_system):
    sessions, contexts, _, pipeline = generation_system
    event_id = add_event(sessions, contexts["AUTO-N8N-01"])
    result = EventProcessor(sessions, pipeline).process_event(event_id)
    assert result.status == "ANSWERED"

    with sessions() as session:
        answer = session.scalar(select(Answer).where(Answer.event_id == event_id))
        outbox = session.scalar(select(OutboxMessage).where(OutboxMessage.event_id == event_id))
        assert answer is not None
        assert outbox.status == "PENDING"
        assert outbox.kind == "ANSWER_ACK"
        assert outbox.payload["body"] == answer.reply_text
        assert outbox.payload["citations"] == answer.source_chunks
        assert outbox.message_key.startswith("mock:v1:")
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutboxMessage)
                .where(OutboxMessage.event_id == event_id)
            )
            == 1
        )


def test_handoff_creates_ticket_and_safe_ack_outbox(generation_system):
    sessions, contexts, _, pipeline = generation_system
    event_id = add_event(sessions, contexts["AUTO-N8N-01"], "RAG khác Fine-tuning?")
    result = EventProcessor(sessions, pipeline).process_event(event_id)
    assert result.status == "HANDOFF"

    with sessions() as session:
        ticket = session.scalar(select(Ticket).where(Ticket.event_id == event_id))
        outbox = session.scalar(select(OutboxMessage).where(OutboxMessage.event_id == event_id))
        assert ticket.status == "OPEN"
        assert outbox.kind == "HANDOFF_ACK"
        assert "đã được ghi nhận" in outbox.payload["body"]
        assert outbox.payload["citations"] == []


def test_failed_outbox_insert_rolls_back_event_outcome(generation_system, monkeypatch):
    sessions, contexts, _, pipeline = generation_system

    def fail_insert(*args, **kwargs):
        raise RuntimeError("injected outbox write failure")

    monkeypatch.setattr(processor_module, "create_outbox", fail_insert)
    event_id = add_event(sessions, contexts["AUTO-N8N-01"])
    result = EventProcessor(sessions, pipeline).process_event(event_id)
    assert result.status == "FAILED_RETRYABLE"

    with sessions() as session:
        event = session.get(Event, event_id)
        assert event.status == "FAILED_RETRYABLE"
        assert session.scalar(select(Answer).where(Answer.event_id == event_id)) is None
        assert session.scalar(select(Ticket).where(Ticket.event_id == event_id)) is None
        assert (
            session.scalar(select(OutboxMessage).where(OutboxMessage.event_id == event_id)) is None
        )


def test_mock_dispatch_is_idempotent_and_rechecks_sources(generation_system):
    sessions, contexts, _, pipeline = generation_system
    event_id = add_event(sessions, contexts["AUTO-N8N-01"])
    EventProcessor(sessions, pipeline).process_event(event_id)
    worker = DeliveryWorker(sessions, MockReplyAdapter(sessions))

    assert worker.run_once(limit=5) == ["SENT"]
    assert worker.run_once(limit=5) == []
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(MockDeliveredMessage)) == 1
        outbox = session.scalar(select(OutboxMessage).where(OutboxMessage.event_id == event_id))
        assert outbox.status == "SENT"
        assert outbox.provider_message_id.startswith("mock:")


def test_revoked_source_suppresses_before_mock_send(generation_system):
    sessions, contexts, store, pipeline = generation_system
    event_id = add_event(sessions, contexts["AUTO-N8N-01"])
    EventProcessor(sessions, pipeline).process_event(event_id)
    store.revoke("AUTO", "test revocation before delivery")

    worker = DeliveryWorker(sessions, MockReplyAdapter(sessions))
    assert worker.run_once(limit=5) == ["SUPPRESSED"]
    with sessions() as session:
        outbox = session.scalar(select(OutboxMessage).where(OutboxMessage.event_id == event_id))
        assert outbox.status == "SUPPRESSED"
        assert session.scalar(select(func.count()).select_from(MockDeliveredMessage)) == 0


def test_delivery_retry_does_not_rerun_generation(generation_system):
    sessions, contexts, _, pipeline = generation_system

    class CountingProvider:
        calls = 0

        def generate(self, question, evidence):
            self.calls += 1
            from app.llm.provider import FakeLLM

            return FakeLLM().generate(question, evidence)

    provider = CountingProvider()
    pipeline.provider = provider
    event_id = add_event(sessions, contexts["AUTO-N8N-01"])
    EventProcessor(sessions, pipeline).process_event(event_id)

    class FailingAdapter:
        def send(self, _):
            raise DeliveryFailure(retryable=True)

    worker = DeliveryWorker(sessions, FailingAdapter(), max_attempts=2)
    assert worker.run_once(limit=5) == ["RETRY_WAIT"]
    from datetime import UTC, datetime, timedelta

    with sessions.begin() as session:
        outbox = session.scalar(select(OutboxMessage).where(OutboxMessage.event_id == event_id))
        outbox.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    worker.adapter = MockReplyAdapter(sessions)
    assert worker.run_once(limit=5) == ["SENT"]
    assert provider.calls == 1


def test_permanent_delivery_failure_dead_letters_outbox_but_keeps_answer(generation_system):
    sessions, contexts, _, pipeline = generation_system
    event_id = add_event(sessions, contexts["AUTO-N8N-01"])
    EventProcessor(sessions, pipeline).process_event(event_id)

    class PermanentFailure:
        def send(self, _):
            raise DeliveryFailure(retryable=False)

    worker = DeliveryWorker(sessions, PermanentFailure())
    assert worker.run_once(limit=1) == ["DEAD"]
    with sessions() as session:
        assert session.scalar(select(Answer).where(Answer.event_id == event_id)) is not None
        outbox = session.scalar(select(OutboxMessage).where(OutboxMessage.event_id == event_id))
        assert outbox.status == "DEAD"
        assert outbox.last_error_code == "MOCK_DELIVERY_PERMANENT_FAILURE"


def test_retryable_delivery_is_bounded_and_dead_letters_at_limit(generation_system):
    sessions, contexts, _, pipeline = generation_system
    event_id = add_event(sessions, contexts["AUTO-N8N-01"])
    EventProcessor(sessions, pipeline).process_event(event_id)

    class TemporaryFailure:
        def send(self, _):
            raise DeliveryFailure(retryable=True)

    worker = DeliveryWorker(sessions, TemporaryFailure(), max_attempts=2)
    assert worker.run_once(limit=1) == ["RETRY_WAIT"]
    from datetime import UTC, datetime, timedelta

    with sessions.begin() as session:
        outbox = session.scalar(select(OutboxMessage).where(OutboxMessage.event_id == event_id))
        outbox.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    assert worker.run_once(limit=1) == ["DEAD"]
    with sessions() as session:
        outbox = session.scalar(select(OutboxMessage).where(OutboxMessage.event_id == event_id))
        assert outbox.attempts == 2
        assert outbox.last_error_code == "DELIVERY_RETRIES_EXHAUSTED"


def test_crash_after_receipt_before_sent_reuses_same_mock_receipt(generation_system):
    sessions, contexts, _, pipeline = generation_system
    event_id = add_event(sessions, contexts["AUTO-N8N-01"])
    EventProcessor(sessions, pipeline).process_event(event_id)

    class LostAckWorker(DeliveryWorker):
        def _finish(self, claim, *, status, **kwargs):
            if status == "SENT":
                return None
            return super()._finish(claim, status=status, **kwargs)

    adapter = MockReplyAdapter(sessions)
    first_worker = LostAckWorker(sessions, adapter, max_attempts=2)
    assert first_worker.run_once(limit=5) == [None]
    from datetime import UTC, datetime, timedelta

    with sessions.begin() as session:
        outbox = session.scalar(select(OutboxMessage).where(OutboxMessage.event_id == event_id))
        outbox.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    second_worker = DeliveryWorker(sessions, adapter, max_attempts=2)
    assert second_worker.run_once(limit=5) == ["SENT"]
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(MockDeliveredMessage)) == 1


def test_concurrent_dispatchers_have_one_current_delivery_owner(generation_system):
    sessions, contexts, _, pipeline = generation_system
    event_id = add_event(sessions, contexts["AUTO-N8N-01"])
    EventProcessor(sessions, pipeline).process_event(event_id)
    barrier = Barrier(2)

    class SynchronizedWorker(DeliveryWorker):
        def run_once(self, **kwargs):
            barrier.wait(timeout=10)
            return super().run_once(**kwargs)

    adapter = MockReplyAdapter(sessions)
    workers = [SynchronizedWorker(sessions, adapter) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda worker: worker.run_once(limit=1), workers))

    assert sum(result.count("SENT") for result in results) == 1
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(MockDeliveredMessage)) == 1


@pytest.mark.parametrize("change", ["rebind", "disable"])
def test_rebound_group_or_disabled_class_suppresses_delivery(generation_system, change):
    sessions, contexts, _, pipeline = generation_system
    event_id = add_event(sessions, contexts["AUTO-N8N-01"])
    EventProcessor(sessions, pipeline).process_event(event_id)
    with sessions.begin() as session:
        if change == "rebind":
            binding = session.get(ChannelBinding, contexts["AUTO-N8N-01"].binding_id)
            binding.class_id = contexts["GS-KPI-01"].class_id
        else:
            session.get(Classroom, contexts["AUTO-N8N-01"].class_id).bot_enabled = False

    worker = DeliveryWorker(sessions, MockReplyAdapter(sessions))
    assert worker.run_once(limit=5) == ["SUPPRESSED"]
    with sessions() as session:
        outbox = session.scalar(select(OutboxMessage).where(OutboxMessage.event_id == event_id))
        assert outbox.status == "SUPPRESSED"
        assert session.scalar(select(func.count()).select_from(MockDeliveredMessage)) == 0


def test_stale_delivery_lease_cannot_write_mock_receipt(generation_system):
    sessions, contexts, _, pipeline = generation_system
    event_id = add_event(sessions, contexts["AUTO-N8N-01"])
    EventProcessor(sessions, pipeline).process_event(event_id)
    worker = DeliveryWorker(sessions, MockReplyAdapter(sessions))
    first = worker.queue.claim_due(limit=1, worker_id="stale-delivery")
    assert len(first) == 1
    with sessions.begin() as session:
        row = session.get(OutboxMessage, first[0].outbox_id)
        row.lease_expires_at = utc_now().replace(year=2020)
    replacement = worker.queue.claim_due(limit=1, worker_id="current-delivery")
    assert len(replacement) == 1
    with pytest.raises(DeliveryLeaseLost):
        MockReplyAdapter(sessions).send(worker.queue.get_message(first[0]))
    assert worker._finish(first[0], status="DEAD") is None
    with sessions.begin() as session:
        row = session.get(OutboxMessage, first[0].outbox_id)
        row.lease_expires_at = utc_now().replace(year=2020)
    assert worker.run_once(limit=1, worker_id="current-delivery") == ["SENT"]
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(MockDeliveredMessage)) == 1
