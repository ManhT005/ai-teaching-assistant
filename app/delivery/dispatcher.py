"""Fenced outbox dispatch with bounded retries and sanitized error codes."""

from datetime import timedelta
from threading import Event as StopEvent

from sqlalchemy import func, select

from app.delivery.contracts import DeliveryFailure, DeliveryLeaseLost, DeliverySuppressed
from app.delivery.outbox import OutboxQueue
from app.events.retry import RetryPolicy
from app.models import OutboxMessage


class DeliveryWorker:
    def __init__(self, sessions, adapter, *, lease_seconds=60, max_attempts=3):
        self.sessions = sessions
        self.adapter = adapter
        self.queue = OutboxQueue(sessions, lease_seconds=lease_seconds, max_attempts=max_attempts)
        self.max_attempts = max_attempts
        self.retry_policy = RetryPolicy(base_seconds=2, cap_seconds=60)

    def run_once(self, *, limit=10, worker_id="delivery-worker"):
        if not 1 <= limit <= 100:
            raise ValueError("Dispatch limit must be between 1 and 100")
        statuses = []
        for _ in range(limit):
            # Never lease messages in bulk while earlier sends might block.
            claims = self.queue.claim_due(limit=1, worker_id=worker_id)
            if not claims:
                break
            claim = claims[0]
            if not self.queue.is_current(claim):
                continue
            message = self.queue.get_message(claim)
            try:
                receipt = self.adapter.send(message)
            except DeliveryLeaseLost:
                status = None
            except DeliverySuppressed:
                status = self._finish(claim, status="SUPPRESSED", error="DELIVERY_SCOPE_REVOKED")
            except DeliveryFailure as error:
                status = self._fail(claim, retryable=error.retryable)
            except Exception:  # noqa: BLE001 -- never log adapter exception text
                status = self._fail(claim, retryable=True)
            else:
                status = self._finish(
                    claim,
                    status="SENT",
                    provider_message_id=receipt.external_message_id,
                )
            statuses.append(status)
        return statuses

    def run_forever(
        self,
        *,
        poll_interval=1.0,
        limit=10,
        worker_id="delivery-worker",
        stop: StopEvent | None = None,
    ):
        stop = stop or StopEvent()
        while not stop.is_set():
            self.run_once(limit=limit, worker_id=worker_id)
            stop.wait(poll_interval)

    def _fail(self, claim, *, retryable):
        retry = retryable and claim.attempts < self.max_attempts
        return self._finish(
            claim,
            status="RETRY_WAIT" if retry else "DEAD",
            error=(
                "MOCK_DELIVERY_FAILURE"
                if retry
                else "DELIVERY_RETRIES_EXHAUSTED"
                if retryable
                else "MOCK_DELIVERY_PERMANENT_FAILURE"
            ),
            retry_delay=self.retry_policy.delay_seconds(claim.attempts) if retry else None,
        )

    def _finish(
        self,
        claim,
        *,
        status,
        error=None,
        provider_message_id=None,
        sent_at=None,
        next_attempt_at=None,
        retry_delay=None,
    ):
        with self.sessions.begin() as session:
            row = session.get(OutboxMessage, claim.outbox_id, with_for_update=True)
            database_now = session.scalar(select(func.now()))
            if not (
                row
                and row.status == "SENDING"
                and row.lease_token == claim.lease_token
                and row.lease_expires_at is not None
                and row.lease_expires_at > database_now
            ):
                return None
            row.status = status
            row.last_error_code = error
            row.provider_message_id = provider_message_id
            row.sent_at = database_now if status == "SENT" else sent_at
            row.next_attempt_at = (
                database_now + timedelta(seconds=retry_delay)
                if retry_delay is not None
                else next_attempt_at
            )
            row.lease_token = None
            row.lease_expires_at = None
            return status
