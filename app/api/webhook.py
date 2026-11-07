"""Authenticated local mock event receiver; operators process stored events separately.

This endpoint persists one accepted event before acknowledgement. It does NOT
call an LLM, create a reply, or claim exactly-once external delivery.
"""
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.mock_channel import question_from_text
from app.models import ChannelBinding, Event
from app.schemas import MockMessageEvent, WebhookAck

router = APIRouter(prefix="/webhook", tags=["webhook"])


def require_mock_auth(
    x_webhook_token: str | None = Header(default=None),
) -> None:
    configured = get_settings().mock_webhook_token
    if not configured:
        raise HTTPException(status_code=503, detail="Mock webhook is not configured")
    if not x_webhook_token or not secrets.compare_digest(x_webhook_token, configured):
        raise HTTPException(status_code=401, detail="Invalid webhook credential")


@router.post("/mock", response_model=WebhookAck, dependencies=[Depends(require_mock_auth)])
def mock_event(
    payload: MockMessageEvent,
    response: Response,
    db: Session = Depends(get_db),
):
    binding = db.scalar(
        select(ChannelBinding).where(
            ChannelBinding.channel == payload.channel,
            ChannelBinding.group_id == payload.group_id,
            ChannelBinding.active.is_(True),
        )
    )
    if binding is None:
        raise HTTPException(status_code=403, detail="Group is not bound to a class")
    if payload.is_bot or question_from_text(payload.text) is None:
        return WebhookAck(status="ignored", event_id=payload.event_id)
    if not binding.classroom.bot_enabled:
        return WebhookAck(status="ignored", event_id=payload.event_id, detail="Bot disabled for this class")

    query = (
        insert(Event)
        .values(
            channel=payload.channel,
            external_event_id=payload.event_id,
            group_id=payload.group_id,
            class_id=binding.class_id,
            sender_id=payload.sender_id,
            body=payload.text,
            source_received_at=payload.received_at,
            status="PENDING",
        )
        .on_conflict_do_nothing(constraint="uq_event_external")
        .returning(Event.id)
    )
    created_id = db.scalar(query)
    db.commit()
    if created_id is None:
        return WebhookAck(status="duplicate", event_id=payload.event_id)

    response.status_code = status.HTTP_202_ACCEPTED
    return WebhookAck(status="accepted", event_id=payload.event_id, detail="Stored; awaiting operator processing")

