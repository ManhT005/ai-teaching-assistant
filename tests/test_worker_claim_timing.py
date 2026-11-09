"""Regression: polling must not pre-lease a batch before slow work starts."""

from types import SimpleNamespace

import pytest

from app.delivery.contracts import DeliveryReceipt
from app.delivery.dispatcher import DeliveryWorker
from app.events.worker import EventWorker


class SequentialQueue:
    def __init__(self, count):
        self.pending = list(range(1, count + 1))
        self.in_flight = False
        self.claimed = []

    def claim_due(self, *, limit, worker_id):
        assert limit == 1, "Workers must never pre-lease a batch"
        assert not self.in_flight, "Previous leased item has not finished"
        if not self.pending:
            return []
        item = self.pending.pop(0)
        self.in_flight = True
        self.claimed.append(item)
        return [SimpleNamespace(event_id=item, outbox_id=item, lease_token=f"lease-{item}")]


class FakeProcessor:
    def __init__(self, queue):
        self.queue = queue
        self.processed = []

    def process_claim(self, claim):
        assert self.queue.in_flight
        self.processed.append(claim.event_id)
        self.queue.in_flight = False
        return SimpleNamespace(status="ANSWERED")


class DeliveryQueue(SequentialQueue):
    def is_current(self, claim):
        return True

    def get_message(self, claim):
        return SimpleNamespace(outbox_id=claim.outbox_id)


class FakeAdapter:
    def __init__(self, queue):
        self.queue = queue

    def send(self, message):
        assert self.queue.in_flight
        self.queue.in_flight = False
        return DeliveryReceipt(external_message_id=f"mock:{message.outbox_id}", duplicate=False)


def test_event_worker_claims_one_at_a_time_without_leasing_waiting_items():
    queue = SequentialQueue(3)
    processor = FakeProcessor(queue)
    worker = EventWorker(queue, processor)

    assert [result.status for result in worker.run_once(limit=3)] == ["ANSWERED"] * 3
    assert queue.claimed == [1, 2, 3]
    assert processor.processed == [1, 2, 3]


def test_delivery_worker_claims_each_message_after_the_previous_send():
    queue = DeliveryQueue(3)
    worker = DeliveryWorker(None, FakeAdapter(queue))
    worker.queue = queue
    worker._finish = lambda claim, *, status, **kwargs: status

    assert worker.run_once(limit=3) == ["SENT"] * 3
    assert queue.claimed == [1, 2, 3]


@pytest.mark.parametrize("invalid", [0, 101])
def test_worker_limits_reject_invalid_batches(invalid):
    queue = SequentialQueue(1)
    with pytest.raises(ValueError):
        EventWorker(queue, FakeProcessor(queue)).run_once(limit=invalid)
    with pytest.raises(ValueError):
        DeliveryWorker(None, FakeAdapter(queue)).run_once(limit=invalid)
