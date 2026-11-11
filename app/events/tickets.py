"""Persist tickets inside the processor's final, event-locked transaction."""

from sqlalchemy import select

from app import workflow_models  # noqa: F401 -- register staff foreign-key targets for Ticket.
from app.generation.contracts import ReasonCode, Usage
from app.models import Event, ModelUsage, Ticket


def create_ticket(session, event: Event, reason: ReasonCode) -> Ticket:
    existing = session.scalar(select(Ticket).where(Ticket.event_id == event.id))
    if existing is not None:
        return existing

    ticket = Ticket(
        event_id=event.id,
        class_id=event.class_id,
        reason=reason.value,
        reason_code=reason.value,
        status="OPEN",
        evidence_summary={"validation": "rejected", "reason_code": reason.value},
    )
    session.add(ticket)
    session.flush()
    return ticket


def record_usage(session, event_id: int, usage: Usage | None) -> None:
    if usage is not None:
        session.add(ModelUsage(event_id=event_id, **usage.model_dump()))
