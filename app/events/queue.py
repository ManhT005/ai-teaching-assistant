"""Short PostgreSQL transactions for claiming and fencing event work."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import or_, select

from app.models import Event


@dataclass(frozen=True)
class ClaimedEvent:
    event_id: int
    lease_token: str
    attempts: int
    lease_expires_at: datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


class EventQueue:
    def __init__(self, sessions, *, lease_seconds: int = 120, max_attempts: int = 3):
        if lease_seconds < 1 or max_attempts < 1:
            raise ValueError("Lease and attempt limits must be positive")
        self.sessions = sessions
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts

    def claim_event(self, event_id: int, *, worker_id: str = "inline") -> ClaimedEvent | None:
        with self.sessions.begin() as session:
            event = session.scalar(
                select(Event).where(Event.id == event_id).with_for_update(skip_locked=True)
            )
            if event is None:
                return None
            now = utc_now()
            if event.attempts >= self.max_attempts:
                if event.status in {"PENDING", "FAILED_RETRYABLE"} or (
                    event.status == "PROCESSING"
                    and event.lease_expires_at is not None
                    and event.lease_expires_at <= now
                ):
                    self._exhaust(event, now)
                return None
            if not self._eligible(event, now):
                return None
            return self._claim(event, now, worker_id)

    def claim_due(self, *, limit: int = 10, worker_id: str = "event-worker") -> list[ClaimedEvent]:
        if not 1 <= limit <= 100:
            raise ValueError("Claim limit must be between 1 and 100")
        claimed = []
        with self.sessions.begin() as session:
            now = utc_now()
            due = or_(Event.next_attempt_at.is_(None), Event.next_attempt_at <= now)
            candidates = list(
                session.scalars(
                    select(Event)
                    .where(
                        or_(
                            (Event.status == "PENDING") & due,
                            (Event.status == "FAILED_RETRYABLE") & due,
                            (Event.status == "PROCESSING") & (Event.lease_expires_at <= now),
                        )
                    )
                    .order_by(Event.received_at, Event.id)
                    .limit(limit * 2)
                    .with_for_update(skip_locked=True)
                )
            )
            for event in candidates:
                if event.attempts >= self.max_attempts:
                    self._exhaust(event, now)
                    continue
                if len(claimed) >= limit:
                    break
                claimed.append(self._claim(event, now, worker_id))
        return claimed

    def is_current(self, claim: ClaimedEvent) -> bool:
        with self.sessions() as session:
            event = session.get(Event, claim.event_id)
            return bool(
                event
                and event.status == "PROCESSING"
                and event.lease_token == claim.lease_token
                and event.lease_expires_at is not None
                and event.lease_expires_at > utc_now()
            )

    def _claim(self, event: Event, now: datetime, worker_id: str) -> ClaimedEvent:
        token = str(uuid4())
        expires = now + timedelta(seconds=self.lease_seconds)
        event.status = "PROCESSING"
        event.attempts += 1
        event.processing_started_at = now
        event.finished_at = None
        event.last_error_code = None
        event.error_category = None
        event.lease_token = token
        event.lease_expires_at = expires
        event.worker_id = worker_id[:120]
        event.next_attempt_at = None
        return ClaimedEvent(event.id, token, event.attempts, expires)

    @staticmethod
    def _eligible(event: Event, now: datetime) -> bool:
        if event.status == "PENDING":
            return event.next_attempt_at is None or event.next_attempt_at <= now
        if event.status == "FAILED_RETRYABLE":
            return event.next_attempt_at is not None and event.next_attempt_at <= now
        return (
            event.status == "PROCESSING"
            and event.lease_expires_at is not None
            and event.lease_expires_at <= now
        )

    @staticmethod
    def _exhaust(event: Event, now: datetime) -> None:
        event.status = "FAILED_FINAL"
        event.last_error_code = "RETRIES_EXHAUSTED"
        event.error_category = "retry_exhausted"
        event.finished_at = now
        event.lease_token = None
        event.lease_expires_at = None
        event.worker_id = None
