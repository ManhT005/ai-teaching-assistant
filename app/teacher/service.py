"""Locked ticket transitions and append-only action idempotency."""

from sqlalchemy import func, select

from app.models import Event, Ticket
from app.retrieval.service import ScopeAuthorizationError, resolve_authorized_scope
from app.teacher.auth import granted_classes, require_role
from app.teacher.contracts import WorkflowError, digest, preview
from app.workflow_models import TeacherReply, TeacherReplyOutbox, TicketAction

ALL_ROLES = ("TEACHER", "FAQ_REVIEWER", "OPERATOR")


def origin_scope(session, ticket):
    event = session.get(Event, ticket.event_id)
    try:
        scope = resolve_authorized_scope(session, channel=event.channel, group_id=event.group_id)
    except ScopeAuthorizationError:
        raise WorkflowError("ORIGIN_BINDING_REVOKED") from None
    if (
        event.status != "HANDOFF"
        or event.class_id != ticket.class_id
        or scope.class_id != ticket.class_id
    ):
        raise WorkflowError("ORIGIN_BINDING_REVOKED")
    return event, scope


def scoped_ticket(session, identity, ticket_id, *, roles=ALL_ROLES, lock=False):
    statement = select(Ticket).where(
        Ticket.id == ticket_id, Ticket.class_id.in_(granted_classes(session, identity, roles))
    )
    if lock:
        statement = statement.with_for_update()
    ticket = session.scalar(statement)
    if ticket is None:
        raise WorkflowError("WORKFLOW_NOT_FOUND", 404)
    origin_scope(session, ticket)
    return ticket


def replay_action(session, identity, ticket, action, command):
    fingerprint = digest({"action": action, **command.model_dump(mode="json")})
    previous = session.scalar(
        select(TicketAction).where(
            TicketAction.ticket_id == ticket.id, TicketAction.action_key == command.idempotency_key
        )
    )
    if previous is not None:
        if previous.actor_id != identity.actor_id or previous.request_hash != fingerprint:
            raise WorkflowError("IDEMPOTENCY_CONFLICT")
        return True
    if ticket.version != command.expected_version:
        raise WorkflowError("STALE_TICKET_VERSION")
    return False


def audit(session, identity, ticket, action, command, from_state):
    session.add(
        TicketAction(
            ticket_id=ticket.id,
            actor_id=identity.actor_id,
            action=action,
            from_state=from_state,
            to_state=ticket.status,
            expected_version=ticket.version,
            action_key=command.idempotency_key,
            request_hash=digest({"action": action, **command.model_dump(mode="json")}),
        )
    )
    ticket.version += 1
    ticket.last_action_at = session.scalar(select(func.now()))


def ticket_view(session, ticket):
    event = session.get(Event, ticket.event_id)
    reply = session.scalar(select(TeacherReply).where(TeacherReply.ticket_id == ticket.id))
    return {
        "ticket_id": ticket.id,
        "version": ticket.version,
        "status": ticket.status,
        "class_id": ticket.class_id,
        "question_preview": preview(event.body),
        "reason_code": ticket.reason_code,
        "created_at": ticket.created_at,
        "assigned_teacher_id": ticket.assigned_teacher_id,
        "resolved_at": ticket.resolved_at,
        "evidence_summary": ticket.evidence_summary,
        "reply": {
            "reply_id": reply.id,
            "revision": reply.revision,
            "body": reply.body,
            "body_hash": reply.body_hash,
            "status": reply.status,
        }
        if reply
        else None,
    }


def transition(session, identity, ticket_id, action, command):
    roles = ("TEACHER", "OPERATOR") if action == "close" else ("TEACHER",)
    ticket = scoped_ticket(session, identity, ticket_id, roles=roles, lock=True)
    if replay_action(session, identity, ticket, action, command):
        return ticket_view(session, ticket)
    before = ticket.status
    now = session.scalar(select(func.now()))
    if action == "claim":
        if ticket.status != "OPEN":
            raise WorkflowError("TICKET_NOT_OPEN")
        ticket.status, ticket.assigned_teacher_id, ticket.assigned_at = (
            "ASSIGNED",
            identity.actor_id,
            now,
        )
    elif action == "release":
        if ticket.assigned_teacher_id != identity.actor_id or ticket.status not in {
            "ASSIGNED",
            "IN_REVIEW",
        }:
            raise WorkflowError("TICKET_CANNOT_RELEASE")
        ticket.status, ticket.assigned_teacher_id, ticket.assigned_at = "OPEN", None, None
    elif action == "close":
        if ticket.status not in {"OPEN", "ASSIGNED", "IN_REVIEW"}:
            raise WorkflowError("TICKET_CANNOT_CLOSE")
        if ticket.assigned_teacher_id not in {None, identity.actor_id}:
            require_role(session, identity, ticket.class_id, ("OPERATOR",))
        ticket.status, ticket.closure_reason, ticket.closed_at = (
            "CLOSED_NO_REPLY",
            command.reason,
            now,
        )
    elif action in {"draft", "approve"}:
        if ticket.assigned_teacher_id != identity.actor_id or ticket.status not in {
            "ASSIGNED",
            "IN_REVIEW",
        }:
            raise WorkflowError("TICKET_NOT_ASSIGNED_TO_ACTOR")
        reply = session.scalar(
            select(TeacherReply).where(TeacherReply.ticket_id == ticket.id).with_for_update()
        )
        if action == "draft":
            if reply is None:
                reply = TeacherReply(
                    ticket_id=ticket.id,
                    revision=1,
                    body=command.body,
                    body_hash=digest(command.body),
                    author_actor_id=identity.actor_id,
                    status="DRAFT",
                )
                session.add(reply)
            else:
                if reply.status != "DRAFT":
                    raise WorkflowError("REPLY_IMMUTABLE")
                reply.body, reply.body_hash, reply.revision = (
                    command.body,
                    digest(command.body),
                    reply.revision + 1,
                )
                reply.author_actor_id = identity.actor_id
            ticket.status = "IN_REVIEW"
        else:
            if (
                reply is None
                or reply.status != "DRAFT"
                or reply.author_actor_id != identity.actor_id
                or reply.revision != command.reply_revision
                or reply.body_hash != command.approved_body_hash
            ):
                raise WorkflowError("STALE_REPLY_APPROVAL")
            event, scope = origin_scope(session, ticket)
            (
                reply.status,
                reply.approver_actor_id,
                reply.approval_credential_id,
                reply.approved_at,
            ) = "APPROVED", identity.actor_id, identity.credential_id, now
            session.add(
                TeacherReplyOutbox(
                    reply_id=reply.id,
                    message_key=f"teacher-reply:v1:{reply.id}:{reply.revision}",
                    class_id=ticket.class_id,
                    binding_id=scope.binding_id,
                    channel=event.channel,
                    group_id=event.group_id,
                    approved_revision=reply.revision,
                    approved_hash=reply.body_hash,
                    status="PENDING",
                    attempts=0,
                )
            )
            ticket.status = "REPLY_QUEUED"
    else:
        raise WorkflowError("UNKNOWN_TICKET_ACTION", 400)
    audit(session, identity, ticket, action, command, before)
    session.flush()
    return ticket_view(session, ticket)
