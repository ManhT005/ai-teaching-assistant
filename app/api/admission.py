"""Persist a new trusted webhook event and reserve its quotas atomically."""

from sqlalchemy.dialects.postgresql import insert

from app.config import Settings
from app.events.rate_limit import RateLimitService
from app.models import Event
from app.schemas import MockMessageEvent


def admit_event(
    session, payload: MockMessageEvent, *, class_id: int, settings: Settings
) -> int | None:
    """Return the stored row ID, or None for an existing duplicate.

    The caller owns commit/rollback, keeping event insertion and both quota
    reservations in one database transaction.
    """
    created_id = session.scalar(
        insert(Event)
        .values(
            channel=payload.channel,
            external_event_id=payload.event_id,
            group_id=payload.group_id,
            class_id=class_id,
            sender_id=payload.sender_id,
            body=payload.text,
            source_received_at=payload.received_at,
            status="PENDING",
        )
        .on_conflict_do_nothing(constraint="uq_event_external")
        .returning(Event.id)
    )
    if created_id is None:
        return None

    RateLimitService(
        sender_limit=settings.webhook_sender_limit,
        group_limit=settings.webhook_group_limit,
        window_seconds=settings.webhook_rate_window_seconds,
    ).reserve(
        session,
        channel=payload.channel,
        group_id=payload.group_id,
        sender_id=payload.sender_id,
    )
    return created_id
