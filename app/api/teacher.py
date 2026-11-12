"""Internal staff work queue; SQL scope is derived from authenticated grants."""

# ruff: noqa: B008 -- FastAPI dependency and query declarations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.db import get_db
from app.models import ChannelBinding, Classroom, Event, Ticket
from app.teacher.auth import granted_classes, staff_identity
from app.teacher.contracts import CloseTicket, Command, ReplyApproval, ReplyDraft, WorkflowError
from app.teacher.service import ALL_ROLES, scoped_ticket, ticket_view, transition
from app.workflow_models import FAQCandidate, TeacherReply, TeacherReplyOutbox, TicketAction

router = APIRouter(prefix="/internal/teacher", tags=["teacher"])


@router.get("/metrics")
def teacher_metrics(identity=Depends(staff_identity), db=Depends(get_db)):
    classes = granted_classes(db, identity, ALL_ROLES)
    tickets = dict(
        db.execute(
            select(Ticket.status, func.count())
            .where(Ticket.class_id.in_(classes))
            .group_by(Ticket.status)
        ).all()
    )
    replies = dict(
        db.execute(
            select(TeacherReplyOutbox.status, func.count())
            .where(TeacherReplyOutbox.class_id.in_(classes))
            .group_by(TeacherReplyOutbox.status)
        ).all()
    )
    faq = dict(
        db.execute(
            select(FAQCandidate.status, func.count())
            .where(FAQCandidate.class_id.in_(classes))
            .group_by(FAQCandidate.status)
        ).all()
    )
    return {
        "tickets": tickets,
        "teacher_outbox": replies,
        "faq": faq,
        "unresolved_delivery_alerts": replies.get("DEAD", 0) + replies.get("SUPPRESSED", 0),
        "delivery_kind": "LOCAL_MOCK_ONLY",
        "human_handling_sla_seconds": None,
    }


@router.get("/tickets")
def tickets(
    status: str | None = None,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    identity=Depends(staff_identity),
    db=Depends(get_db),
):
    query = (
        select(Ticket)
        .join(Event, Event.id == Ticket.event_id)
        .join(Classroom, Classroom.id == Ticket.class_id)
        .join(
            ChannelBinding,
            (ChannelBinding.channel == Event.channel)
            & (ChannelBinding.group_id == Event.group_id)
            & (ChannelBinding.class_id == Ticket.class_id),
        )
        .where(
            Ticket.class_id.in_(granted_classes(db, identity, ALL_ROLES)),
            Event.status == "HANDOFF",
            Classroom.bot_enabled.is_(True),
            ChannelBinding.active.is_(True),
            Ticket.id > cursor,
        )
        .order_by(Ticket.id)
        .limit(limit)
    )
    if status:
        query = query.where(Ticket.status == status)
    rows = list(db.scalars(query))
    now = db.scalar(select(func.now()))
    return {
        "items": [
            {
                **ticket_view(db, ticket),
                "queue_age_seconds": max(0, (now - ticket.created_at).total_seconds()),
            }
            for ticket in rows
        ],
        "next_cursor": rows[-1].id if len(rows) == limit else None,
    }


@router.get("/tickets/{ticket_id}")
def ticket_detail(ticket_id: int, identity=Depends(staff_identity), db=Depends(get_db)):
    ticket = scoped_ticket(db, identity, ticket_id)
    actions = db.scalars(
        select(TicketAction).where(TicketAction.ticket_id == ticket.id).order_by(TicketAction.id)
    )
    return {
        **ticket_view(db, ticket),
        "actions": [
            {
                "action": row.action,
                "actor_id": row.actor_id,
                "from_state": row.from_state,
                "to_state": row.to_state,
                "occurred_at": row.created_at,
            }
            for row in actions
        ],
    }


def apply(db, identity, ticket_id, action, command):
    result = transition(db, identity, ticket_id, action, command)
    db.commit()
    return result


@router.post("/tickets/{ticket_id}/claim")
def claim_ticket(
    ticket_id: int, command: Command, identity=Depends(staff_identity), db=Depends(get_db)
):
    return apply(db, identity, ticket_id, "claim", command)


@router.post("/tickets/{ticket_id}/reply-draft")
def draft_reply(
    ticket_id: int, command: ReplyDraft, identity=Depends(staff_identity), db=Depends(get_db)
):
    return apply(db, identity, ticket_id, "draft", command)


@router.post("/tickets/{ticket_id}/release")
def release_ticket(
    ticket_id: int, command: Command, identity=Depends(staff_identity), db=Depends(get_db)
):
    return apply(db, identity, ticket_id, "release", command)


@router.post("/tickets/{ticket_id}/approve-reply")
def approve_reply(
    ticket_id: int, command: ReplyApproval, identity=Depends(staff_identity), db=Depends(get_db)
):
    return apply(db, identity, ticket_id, "approve", command)


@router.post("/tickets/{ticket_id}/close")
def close_ticket(
    ticket_id: int, command: CloseTicket, identity=Depends(staff_identity), db=Depends(get_db)
):
    return apply(db, identity, ticket_id, "close", command)


@router.get("/replies/{reply_id}/delivery")
def reply_delivery(reply_id: int, identity=Depends(staff_identity), db=Depends(get_db)):
    reply = db.scalar(
        select(TeacherReply)
        .join(Ticket, Ticket.id == TeacherReply.ticket_id)
        .where(
            TeacherReply.id == reply_id,
            Ticket.class_id.in_(granted_classes(db, identity, ALL_ROLES)),
        )
    )
    if reply is None:
        raise WorkflowError("WORKFLOW_NOT_FOUND", 404)
    row = db.scalar(select(TeacherReplyOutbox).where(TeacherReplyOutbox.reply_id == reply.id))
    return {
        "reply_id": reply.id,
        "delivery_status": row.status if row else None,
        "attempts": row.attempts if row else 0,
        "last_error_code": row.last_error_code if row else None,
        "sent_at": row.sent_at if row else None,
        "delivery_kind": "LOCAL_MOCK_ONLY",
    }
