"""Operator-only aggregate status and local mock inbox inspection."""

import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import case, extract, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import (
    Answer,
    Classroom,
    Event,
    MockDeliveredMessage,
    ModelUsage,
    OutboxMessage,
    Ticket,
)


def require_operator_auth(x_operator_token: str | None = Header(default=None)) -> None:
    configured = get_settings().operator_api_token
    if not configured:
        raise HTTPException(status_code=503, detail="Operator access is not configured")
    if not x_operator_token or not secrets.compare_digest(x_operator_token, configured):
        raise HTTPException(status_code=401, detail="Invalid operator credential")


router = APIRouter(
    prefix="/internal/demo",
    tags=["internal-demo"],
    dependencies=[Depends(require_operator_auth)],
)


@router.get("/metrics")
def operations_metrics(db: Session = Depends(get_db)):  # noqa: B008 -- FastAPI dependency.
    status_rows = db.execute(select(Event.status, func.count()).group_by(Event.status)).all()
    event_counts = {status: count for status, count in status_rows}

    class_rows = db.execute(
        select(Classroom.code, Event.status, func.count())
        .join(Event, Event.class_id == Classroom.id)
        .group_by(Classroom.code, Event.status)
        .order_by(Classroom.code, Event.status)
    ).all()
    by_class: dict[str, dict[str, int]] = {}
    for code, status, count in class_rows:
        by_class.setdefault(code, {})[status] = count

    outbox_rows = db.execute(
        select(OutboxMessage.status, func.count()).group_by(OutboxMessage.status)
    ).all()
    outbox_counts = {status: count for status, count in outbox_rows}
    duration_ms = extract("epoch", Event.finished_at - Event.received_at) * 1000
    p50, p95 = db.execute(
        select(
            func.percentile_cont(0.5).within_group(duration_ms),
            func.percentile_cont(0.95).within_group(duration_ms),
        ).where(Event.finished_at.is_not(None))
    ).one()
    oldest_pending = db.scalar(
        select(func.min(Event.received_at)).where(Event.status.in_(("PENDING", "FAILED_RETRYABLE")))
    )
    oldest_outbox = db.scalar(
        select(func.min(OutboxMessage.created_at)).where(
            OutboxMessage.status.in_(("PENDING", "RETRY_WAIT"))
        )
    )
    now = datetime.now(UTC)
    known_cost = db.scalar(select(func.sum(ModelUsage.estimated_cost_usd)))
    unknown_cost_events = db.scalar(
        select(func.count(func.distinct(ModelUsage.event_id))).where(
            ModelUsage.estimated_cost_usd.is_(None)
        )
    )
    usage = db.execute(
        select(
            func.count(ModelUsage.id),
            func.sum(ModelUsage.prompt_tokens),
            func.sum(ModelUsage.completion_tokens),
        )
    ).one()

    return {
        "events": event_counts,
        "by_class": by_class,
        "retry_attempts": sum(
            (row[0] or 0)
            for row in db.execute(
                select(func.sum(case((Event.attempts > 1, Event.attempts - 1), else_=0)))
            ).all()
        ),
        "outbox": outbox_counts,
        "mock_delivered": db.scalar(select(func.count()).select_from(MockDeliveredMessage)),
        "queue": {
            "oldest_due_event_age_seconds": _age_seconds(oldest_pending, now),
            "oldest_due_outbox_age_seconds": _age_seconds(oldest_outbox, now),
            "expired_processing_leases": db.scalar(
                select(func.count())
                .select_from(Event)
                .where(Event.status == "PROCESSING", Event.lease_expires_at <= now)
            ),
        },
        "latency_ms": {
            "p50": float(p50) if p50 is not None else None,
            "p95": float(p95) if p95 is not None else None,
        },
        "usage": {
            "rows": usage[0],
            "prompt_tokens": usage[1],
            "completion_tokens": usage[2],
            "estimated_cost_usd": float(known_cost) if known_cost is not None else None,
            "events_with_unknown_cost": unknown_cost_events,
        },
    }


@router.get("/events/{event_id}")
def event_status(event_id: int, db: Session = Depends(get_db)):  # noqa: B008 -- FastAPI dependency.
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    classroom = db.get(Classroom, event.class_id)
    answer = db.scalar(select(Answer).where(Answer.event_id == event_id))
    ticket = db.scalar(select(Ticket).where(Ticket.event_id == event_id))
    outbox = db.scalar(select(OutboxMessage).where(OutboxMessage.event_id == event_id))
    reason_code = event.last_error_code
    if answer:
        reason_code = reason_code or (answer.validation_summary or {}).get("reason_code")
    elif ticket:
        reason_code = reason_code or ticket.reason_code
    return {
        "event_id": event.id,
        "class_code": classroom.code,
        "status": event.status,
        "attempts": event.attempts,
        "reason_code": reason_code,
        "received_at": event.received_at,
        "finished_at": event.finished_at,
        "decision": "ANSWER" if answer else "HANDOFF" if ticket else None,
        "citation_count": len(answer.source_chunks) if answer else 0,
        "ticket_status": ticket.status if ticket else None,
        "delivery_status": outbox.status if outbox else None,
    }


@router.get("/deliveries")
def mock_deliveries(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),  # noqa: B008 -- FastAPI dependency.
):
    rows = db.execute(
        select(MockDeliveredMessage, Classroom.code, OutboxMessage.kind)
        .join(Classroom, Classroom.id == MockDeliveredMessage.class_id)
        .join(OutboxMessage, OutboxMessage.id == MockDeliveredMessage.outbox_id)
        .order_by(MockDeliveredMessage.delivered_at.desc(), MockDeliveredMessage.id.desc())
        .limit(limit)
    ).all()
    return {
        "items": [
            {
                "delivery_id": message.id,
                "class_code": class_code,
                "kind": db.scalar(
                    select(OutboxMessage.kind).where(OutboxMessage.id == message.outbox_id)
                ),
                "body": message.body,
                "citations": message.citations,
                "delivered_at": message.delivered_at,
            }
            for message, class_code, kind in rows
        ]
    }


def _age_seconds(value, now):
    return max(0.0, (now - value).total_seconds()) if value is not None else None
