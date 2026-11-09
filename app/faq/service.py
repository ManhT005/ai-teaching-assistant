"""FAQ editorial workflow. Review is separate from sending a human reply."""

from sqlalchemy import func, select

from app.models import Classroom, Ticket
from app.teacher.auth import granted_classes, require_role
from app.teacher.contracts import WorkflowError, digest
from app.teacher.service import ALL_ROLES, audit, origin_scope, scoped_ticket
from app.workflow_models import (
    FAQCandidate,
    FAQPublication,
    FAQRevision,
    TeacherReply,
    TicketAction,
)


def content_hash(content):
    return digest(
        {
            key: getattr(content, key)
            for key in ("question", "answer", "topics", "source_references")
        }
    )


def scoped_candidate(session, identity, faq_id, *, roles=ALL_ROLES, lock=False):
    query = select(FAQCandidate).where(
        FAQCandidate.id == faq_id,
        FAQCandidate.class_id.in_(granted_classes(session, identity, roles)),
    )
    if lock:
        # Ticket -> candidate lock order matches create and shared audit writes.
        ticket_id = session.scalar(
            select(FAQCandidate.ticket_id).where(
                FAQCandidate.id == faq_id,
                FAQCandidate.class_id.in_(granted_classes(session, identity, roles)),
            )
        )
        if ticket_id is not None:
            session.get(Ticket, ticket_id, with_for_update=True)
        query = query.with_for_update()
    candidate = session.scalar(query)
    if candidate is None:
        raise WorkflowError("WORKFLOW_NOT_FOUND", 404)
    origin_scope(session, session.get(Ticket, candidate.ticket_id))
    return candidate


def current_revision(session, candidate):
    revision = session.scalar(
        select(FAQRevision).where(
            FAQRevision.faq_id == candidate.id, FAQRevision.revision == candidate.revision
        )
    )
    if revision is None or revision.body_hash != content_hash(revision):
        raise WorkflowError("FAQ_CONTENT_MISMATCH")
    return revision


def faq_view(session, candidate):
    revision = current_revision(session, candidate)
    publication = session.scalar(
        select(FAQPublication).where(
            FAQPublication.faq_id == candidate.id, FAQPublication.revision == candidate.revision
        )
    )
    return {
        "faq_id": candidate.id,
        "ticket_id": candidate.ticket_id,
        "class_scope": session.get(Classroom, candidate.class_id).code,
        "revision": candidate.revision,
        "status": candidate.status,
        "question": revision.question,
        "answer": revision.answer,
        "topics": revision.topics,
        "source_references": revision.source_references,
        "body_hash": revision.body_hash,
        "author_actor_id": candidate.author_actor_id,
        "reviewer_actor_id": revision.reviewer_actor_id,
        "reviewed_at": revision.reviewed_at,
        "review_reason": revision.review_reason,
        "data_origin": "synthetic_demo",
        "publication_status": publication.status if publication else None,
        "document_id": publication.document_id if publication else None,
    }


def create_candidate(session, identity, command):
    ticket = scoped_ticket(session, identity, command.ticket_id, roles=("TEACHER",), lock=True)
    prior = session.scalar(
        select(FAQCandidate).where(FAQCandidate.creation_key == command.idempotency_key)
    )
    if prior is not None:
        if prior.ticket_id != ticket.id or prior.author_actor_id != identity.actor_id:
            raise WorkflowError("IDEMPOTENCY_CONFLICT")
        action = session.scalar(
            select(TicketAction).where(
                TicketAction.ticket_id == ticket.id,
                TicketAction.action_key == command.idempotency_key,
            )
        )
        if action is None or action.request_hash != digest(
            {"action": "faq-create", **command.model_dump(mode="json")}
        ):
            raise WorkflowError("IDEMPOTENCY_CONFLICT")
        return faq_view(session, prior)
    if ticket.version != command.expected_version or ticket.status != "RESOLVED":
        raise WorkflowError("FAQ_REQUIRES_RESOLVED_TICKET")
    reply = session.scalar(
        select(TeacherReply).where(
            TeacherReply.ticket_id == ticket.id, TeacherReply.status == "SENT"
        )
    )
    if reply is None:
        raise WorkflowError("FAQ_REQUIRES_SENT_REPLY")
    candidate = FAQCandidate(
        ticket_id=ticket.id,
        reply_id=reply.id,
        class_id=ticket.class_id,
        author_actor_id=identity.actor_id,
        creation_key=command.idempotency_key,
        revision=1,
        status="DRAFT",
    )
    session.add(candidate)
    session.flush()
    session.add(
        FAQRevision(
            faq_id=candidate.id,
            revision=1,
            question=command.question,
            answer=command.answer,
            topics=command.topics,
            source_references=command.source_references,
            body_hash=content_hash(command),
        )
    )
    audit(session, identity, ticket, "faq-create", command, ticket.status)
    session.flush()
    return faq_view(session, candidate)


def edit_candidate(session, identity, faq_id, action, command):
    roles = ("FAQ_REVIEWER",) if action == "review" else ("TEACHER",)
    candidate = scoped_candidate(session, identity, faq_id, roles=roles, lock=True)
    ticket = session.get(Ticket, candidate.ticket_id)
    action_name = f"faq-{action}:{faq_id}"
    fingerprint = digest({"action": action_name, **command.model_dump(mode="json")})
    existing = session.scalar(
        select(TicketAction).where(
            TicketAction.ticket_id == ticket.id, TicketAction.action_key == command.idempotency_key
        )
    )
    if existing:
        if existing.actor_id != identity.actor_id or existing.request_hash != fingerprint:
            raise WorkflowError("IDEMPOTENCY_CONFLICT")
        return faq_view(session, candidate)
    if candidate.revision != command.expected_version:
        raise WorkflowError("STALE_FAQ_REVISION")
    revision = current_revision(session, candidate)
    if action == "review":
        if candidate.status != "SUBMITTED" or revision.body_hash != command.candidate_body_hash:
            raise WorkflowError("STALE_FAQ_APPROVAL")
        if candidate.author_actor_id == identity.actor_id:
            raise WorkflowError("INDEPENDENT_FAQ_REVIEW_REQUIRED", 403)
        revision.reviewer_actor_id, revision.reviewer_credential_id = (
            identity.actor_id,
            identity.credential_id,
        )
        revision.reviewed_at, revision.review_reason, revision.decision = (
            session.scalar(select(func.now())),
            command.reason,
            command.decision,
        )
        candidate.status = "APPROVED" if command.decision == "approve" else "REJECTED"
    else:
        if candidate.author_actor_id != identity.actor_id:
            raise WorkflowError("FAQ_AUTHOR_REQUIRED", 403)
        if action == "draft":
            if candidate.status not in {"DRAFT", "REJECTED", "PUBLISHED", "FAILED"}:
                raise WorkflowError("FAQ_REVISION_NOT_EDITABLE")
            candidate.revision += 1
            session.add(
                FAQRevision(
                    faq_id=candidate.id,
                    revision=candidate.revision,
                    question=command.question,
                    answer=command.answer,
                    topics=command.topics,
                    source_references=command.source_references,
                    body_hash=content_hash(command),
                )
            )
            candidate.status = "DRAFT"
        elif action == "submit":
            if candidate.status != "DRAFT":
                raise WorkflowError("FAQ_NOT_DRAFT")
            candidate.status, revision.submitted_at = (
                "SUBMITTED",
                session.scalar(select(func.now())),
            )
        else:
            raise WorkflowError("UNKNOWN_FAQ_ACTION", 400)
    audit(session, identity, ticket, action_name, command, ticket.status)
    session.flush()
    return faq_view(session, candidate)


def require_publication_review(session, candidate, revision):
    from app.teacher.auth import StaffIdentity

    if (
        revision.decision != "approve"
        or revision.reviewer_actor_id == candidate.author_actor_id
        or revision.reviewed_at is None
    ):
        raise WorkflowError("FAQ_APPROVAL_REQUIRED")
    require_role(
        session,
        StaffIdentity(revision.reviewer_actor_id, revision.reviewer_credential_id),
        candidate.class_id,
        ("FAQ_REVIEWER",),
    )
