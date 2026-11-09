"""Fenced FAQ publication via existing manifest/parser/KnowledgeStore contracts."""

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select

from app.faq.export import export_faq
from app.faq.service import current_revision, faq_view, require_publication_review, scoped_candidate
from app.models import Classroom, Document, DocumentChunk, Ticket
from app.teacher.contracts import WorkflowError, digest
from app.teacher.service import audit
from app.workflow_models import FAQCandidate, FAQPublication, TicketAction


class FAQPublisher:
    def __init__(self, sessions, store, output_dir: Path):
        self.sessions = sessions
        self.store = store
        self.output_dir = output_dir

    def publish(self, identity, faq_id, command):
        token = str(uuid4())
        with self.sessions.begin() as session:
            candidate = scoped_candidate(session, identity, faq_id, roles=("OPERATOR",), lock=True)
            revision = current_revision(session, candidate)
            require_publication_review(session, candidate, revision)
            if candidate.revision != command.expected_version:
                raise WorkflowError("STALE_FAQ_REVISION")
            action_name = f"faq-publish:{faq_id}:{revision.revision}"
            previous = session.scalar(
                select(TicketAction).where(
                    TicketAction.ticket_id == candidate.ticket_id,
                    TicketAction.action_key == command.idempotency_key,
                )
            )
            if previous and (
                previous.actor_id != identity.actor_id
                or previous.request_hash
                != digest({"action": action_name, **command.model_dump(mode="json")})
            ):
                raise WorkflowError("IDEMPOTENCY_CONFLICT")
            publication = session.scalar(
                select(FAQPublication)
                .where(
                    FAQPublication.faq_id == faq_id, FAQPublication.revision == candidate.revision
                )
                .with_for_update()
            )
            now = session.scalar(select(func.now()))
            if publication and publication.status == "PUBLISHED":
                document = session.get(Document, publication.document_id)
                if not document or not document.active or document.index_status != "READY":
                    raise WorkflowError("FAQ_ACTIVE_SOURCE_MISSING")
                return faq_view(session, candidate)
            if candidate.status not in {"APPROVED", "FAILED", "INDEXING"}:
                raise WorkflowError("FAQ_APPROVAL_REQUIRED")
            if publication and publication.lease_expires_at and publication.lease_expires_at > now:
                raise WorkflowError("FAQ_PUBLICATION_BUSY")
            if publication is None:
                publication = FAQPublication(
                    faq_id=faq_id,
                    revision=candidate.revision,
                    logical_doc_id=f"teacher-faq-{faq_id}",
                    status="INDEXING",
                )
                session.add(publication)
            candidate.status, publication.status = "INDEXING", "INDEXING"
            publication.lease_token, publication.lease_expires_at = (
                token,
                now + timedelta(minutes=10),
            )
            session.flush()
            publication_id, class_code = (
                publication.id,
                session.get(Classroom, candidate.class_id).code,
            )
            # Detached immutable revision fields leave no connection held during embedding.
            session.expunge(revision)
            session.expunge(candidate)
        try:
            sections, _, _, checksum, manifest_checksum = export_faq(
                self.output_dir, candidate, revision, class_code
            )
            with self.sessions.begin() as session:
                row = session.get(FAQPublication, publication_id, with_for_update=True)
                if row.lease_token != token:
                    raise WorkflowError("FAQ_PUBLICATION_LEASE_LOST")
                row.artifact_checksum, row.manifest_checksum = checksum, manifest_checksum

            def activate(session, document):
                current = scoped_candidate(
                    session, identity, faq_id, roles=("OPERATOR",), lock=True
                )
                current_review = current_revision(session, current)
                require_publication_review(session, current, current_review)
                row = session.get(FAQPublication, publication_id, with_for_update=True)
                now = session.scalar(select(func.now()))
                if (
                    row.lease_token != token
                    or row.lease_expires_at <= now
                    or current.status != "INDEXING"
                    or current.revision != revision.revision
                    or current_review.body_hash != revision.body_hash
                ):
                    raise WorkflowError("FAQ_PUBLICATION_LEASE_LOST")
                chunks = list(
                    session.scalars(
                        select(DocumentChunk).where(DocumentChunk.document_id == document.id)
                    )
                )
                if (
                    not document.active
                    or document.index_status != "READY"
                    or document.logical_key != row.logical_doc_id
                    or document.external_version != str(revision.revision)
                    or document.checksum != checksum
                    or not chunks
                    or any(
                        chunk.class_scope != class_code
                        or chunk.source_checksum != checksum
                        or chunk.section_id != f"FAQ-{faq_id}"
                        for chunk in chunks
                    )
                ):
                    raise WorkflowError("FAQ_INDEX_IDENTITY_MISMATCH")
                row.document_id, row.status, row.published_at = document.id, "PUBLISHED", now
                row.lease_token, row.lease_expires_at, row.last_error_code = None, None, None
                current.status = "PUBLISHED"
                ticket = session.get(Ticket, current.ticket_id)
                action_name = f"faq-publish:{faq_id}:{revision.revision}"
                existing = session.scalar(
                    select(TicketAction.id).where(
                        TicketAction.ticket_id == ticket.id,
                        TicketAction.action_key == command.idempotency_key,
                    )
                )
                if existing is not None:
                    raise WorkflowError("IDEMPOTENCY_CONFLICT")
                audit(session, identity, ticket, action_name, command, ticket.status)

            result = self.store.ingest(sections, activation_guard=activate)
            if result["status"] == "skipped":
                # Reconcile a previous active version without embedding or duplicate activation.
                with self.sessions.begin() as session:
                    document = session.get(Document, result["document_id"], with_for_update=True)
                    activate(session, document)
        except Exception:  # noqa: BLE001 -- external/parser exceptions may contain reviewed content
            with self.sessions.begin() as session:
                session.get(Ticket, candidate.ticket_id, with_for_update=True)
                current = session.get(FAQCandidate, faq_id, with_for_update=True)
                row = session.get(FAQPublication, publication_id, with_for_update=True)
                if row.lease_token == token:
                    row.status, row.last_error_code = "FAILED", "FAQ_INDEXING_FAILED"
                    row.lease_token, row.lease_expires_at = None, None
                    if current.status == "INDEXING":
                        current.status = "FAILED"
            raise WorkflowError("FAQ_INDEXING_FAILED", 503) from None
        with self.sessions() as session:
            return faq_view(session, scoped_candidate(session, identity, faq_id))

    def revoke(self, identity, faq_id, command):
        # KnowledgeStore takes the document advisory lock before the candidate lock,
        # matching activation order and making source withdrawal one transaction.
        def withdraw(session):
            candidate = scoped_candidate(session, identity, faq_id, roles=("OPERATOR",), lock=True)
            if candidate.revision != command.expected_version:
                raise WorkflowError("STALE_FAQ_REVISION")
            ticket = session.get(Ticket, candidate.ticket_id)
            action = f"faq-revoke:{faq_id}"
            existing = session.scalar(
                select(TicketAction).where(
                    TicketAction.ticket_id == ticket.id,
                    TicketAction.action_key == command.idempotency_key,
                )
            )
            if existing:
                if existing.actor_id != identity.actor_id or existing.request_hash != digest(
                    {"action": action, **command.model_dump(mode="json")}
                ):
                    raise WorkflowError("IDEMPOTENCY_CONFLICT")
                return
            candidate.status = "REVOKED"
            for row in session.scalars(
                select(FAQPublication).where(FAQPublication.faq_id == faq_id)
            ):
                row.status, row.lease_token, row.lease_expires_at = "REVOKED", None, None
            audit(session, identity, ticket, action, command, ticket.status)

        self.store.revoke(
            f"teacher-faq-{faq_id}", "STAFF_FAQ_WITHDRAWAL", revocation_guard=withdraw
        )
        with self.sessions() as session:
            return faq_view(session, scoped_candidate(session, identity, faq_id))
