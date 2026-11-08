"""Atomic event outcome publication and separately leased outbox dispatch."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import or_, select

from app.delivery.contracts import DeliveryMessage
from app.models import Event, OutboxMessage


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ClaimedOutbox:
    outbox_id: int
    message_key: str
    lease_token: str
    attempts: int


class OutboxQueue:
    def __init__(self, sessions, *, lease_seconds=60, max_attempts=3):
        if lease_seconds < 1 or max_attempts < 1:
            raise ValueError("Delivery lease and attempt limits must be positive")
        self.sessions = sessions
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts

    def claim_due(self, *, limit=10, worker_id="delivery-worker"):
        if not 1 <= limit <= 100:
            raise ValueError("Claim limit must be between 1 and 100")
        claims = []
        with self.sessions.begin() as session:
            now = utc_now()
            due = or_(OutboxMessage.next_attempt_at.is_(None), OutboxMessage.next_attempt_at <= now)
            candidates = list(
                session.scalars(
                    select(OutboxMessage)
                    .where(
                        or_(
                            (OutboxMessage.status == "PENDING") & due,
                            (OutboxMessage.status == "RETRY_WAIT") & due,
                            (OutboxMessage.status == "SENDING")
                            & (OutboxMessage.lease_expires_at <= now),
                        )
                    )
                    .order_by(OutboxMessage.created_at, OutboxMessage.id)
                    .limit(limit * 2)
                    .with_for_update(skip_locked=True)
                )
            )
            for message in candidates:
                if message.attempts >= self.max_attempts:
                    message.status = "DEAD"
                    message.last_error_code = "DELIVERY_RETRIES_EXHAUSTED"
                    message.lease_token = None
                    message.lease_expires_at = None
                    continue
                if len(claims) >= limit:
                    break
                token = str(uuid4())
                message.status = "SENDING"
                message.attempts += 1
                message.lease_token = token
                message.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
                message.next_attempt_at = None
                claims.append(
                    ClaimedOutbox(message.id, message.message_key, token, message.attempts)
                )
        return claims

    def is_current(self, claim: ClaimedOutbox) -> bool:
        with self.sessions() as session:
            row = session.get(OutboxMessage, claim.outbox_id)
            return bool(
                row
                and row.status == "SENDING"
                and row.lease_token == claim.lease_token
                and row.lease_expires_at is not None
                and row.lease_expires_at > utc_now()
            )

    def get_message(self, claim: ClaimedOutbox) -> DeliveryMessage:
        with self.sessions() as session:
            row = session.get(OutboxMessage, claim.outbox_id)
            if row is None or row.message_key != claim.message_key:
                raise ValueError("Outbox claim no longer exists")
            return DeliveryMessage(
                message_key=row.message_key,
                outbox_id=row.id,
                lease_token=claim.lease_token,
                channel=row.channel,
                group_id=row.group_id,
                class_id=row.class_id,
                kind=row.kind,
                body=row.payload["body"],
                citations=list(row.payload.get("citations", [])),
            )


def create_outbox(session, event: Event, *, kind: str, body: str, citations: list[dict]):
    """Insert the one deterministic outbound message inside event finalization."""
    from hashlib import sha256

    key_material = f"mock:v1:{event.channel}:{event.external_event_id}".encode()
    message_key = "mock:v1:" + sha256(key_material).hexdigest()
    message = OutboxMessage(
        event_id=event.id,
        message_key=message_key,
        channel=event.channel,
        group_id=event.group_id,
        class_id=event.class_id,
        kind=kind,
        payload={"body": body, "citations": citations},
        status="PENDING",
        attempts=0,
    )
    session.add(message)
    session.flush()
    return message
