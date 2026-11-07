"""Claim stored events, release DB connections for generation, persist one outcome."""

import logging
from datetime import UTC, datetime

from sqlalchemy import select, update

from app.events.messages import insufficient_evidence_message
from app.events.tickets import create_ticket, record_usage
from app.generation.contracts import Citation, EventProcessingResult, ReasonCode, Usage
from app.generation.evidence import sources_current
from app.llm.provider import ProviderFailure
from app.mock_channel import question_from_text
from app.models import Answer, Event, ModelUsage, Ticket
from app.retrieval.service import (
    ScopeAuthorizationError,
    normalize_question,
    resolve_authorized_scope,
)

logger = logging.getLogger(__name__)


def now():
    return datetime.now(UTC)


class EventProcessor:
    def __init__(self, sessions, pipeline):
        self.sessions = sessions
        self.pipeline = pipeline

    def process_event(self, event_id: int) -> EventProcessingResult:
        if type(event_id) is not int or event_id < 1:
            raise ValueError("Use a positive internal event ID")

        with self.sessions.begin() as session:
            claimed = session.scalar(
                update(Event)
                .where(Event.id == event_id, Event.status == "PENDING")
                .values(
                    status="PROCESSING",
                    attempts=Event.attempts + 1,
                    processing_started_at=now(),
                    finished_at=None,
                    last_error_code=None,
                )
                .returning(Event.id)
            )
            if claimed is None:
                return self._result(session, event_id)

        try:
            with self.sessions() as session:
                event = session.get(Event, event_id)
                scope = resolve_authorized_scope(
                    session,
                    channel=event.channel,
                    group_id=event.group_id,
                )
                if event.class_id != scope.class_id:
                    raise ScopeAuthorizationError("Stored event no longer matches binding")
                question = question_from_text(event.body)

            try:
                question = normalize_question(question)
            except ValueError:
                return self._failure(event_id, ReasonCode.INVALID_EVENT, retryable=False)

            decision = self.pipeline.run(question, scope)
            return self._persist(event_id, scope, decision)

        except ScopeAuthorizationError:
            return self._failure(event_id, ReasonCode.SCOPE_REVOKED, retryable=False)
        except ProviderFailure as error:
            return self._failure(event_id, ReasonCode.PROVIDER_FAILURE, retryable=error.retryable)
        except Exception:  # noqa: BLE001 -- fail closed without exposing exception contents
            # Never log provider bodies, raw SQL parameters, prompts, event text or keys.
            return self._failure(event_id, ReasonCode.PROCESSING_FAILURE, retryable=True)

    def _failure(self, event_id, reason, *, retryable):
        with self.sessions.begin() as session:
            event = session.get(Event, event_id, with_for_update=True)
            if event.status == "PROCESSING":
                event.status = "FAILED_RETRYABLE" if retryable else "FAILED_FINAL"
                event.last_error_code = reason.value
                event.finished_at = now()
            result = self._result(session, event_id)

        self._log(result)
        return result

    def _persist(self, event_id, scope, decision):
        with self.sessions.begin() as session:
            event = session.get(Event, event_id, with_for_update=True)
            if event.status != "PROCESSING":
                return self._result(session, event_id)

            reason = decision.reason
            draft = decision.draft
            try:
                valid = sources_current(
                    session,
                    scope,
                    decision.evidence.hits if draft else (),
                    lock=True,
                )
                valid = valid and event.class_id == scope.class_id
            except ScopeAuthorizationError:
                valid = False

            if not valid:
                # Generation may have incurred real usage even though scope was revoked.
                record_usage(session, event_id, decision.usage)
                event.status = "FAILED_FINAL"
                event.last_error_code = ReasonCode.SCOPE_REVOKED.value
            elif draft is not None and reason == ReasonCode.GROUNDED_ANSWER:
                cited_ids = {chunk_id for claim in draft.claims for chunk_id in claim.citation_ids}
                citations = [
                    Citation(
                        chunk_id=hit.chunk_id,
                        doc_id=hit.doc_id,
                        document_version=hit.version,
                        section_id=hit.section_id,
                        source_name=hit.source_name,
                        locator=hit.locator,
                        class_scope=hit.class_scope,
                    )
                    for hit in decision.evidence.hits
                    if hit.chunk_id in cited_ids
                ]
                numbers = {citation.chunk_id: i for i, citation in enumerate(citations, start=1)}
                reply = "\n".join(
                    f"{index}. {claim.text} "
                    + " ".join(f"[{numbers[chunk_id]}]" for chunk_id in claim.citation_ids)
                    for index, claim in enumerate(draft.claims, start=1)
                )
                session.add(
                    Answer(
                        event_id=event_id,
                        decision="ANSWER",
                        reply_text=reply,
                        source_chunks=[citation.model_dump() for citation in citations],
                        validation_summary={
                            "reason_code": reason.value,
                            "policy": "extractive-paragraph-v1",
                            "claims_validated": len(draft.claims),
                        },
                        provider_request_id=decision.usage.request_id if decision.usage else None,
                    )
                )
                record_usage(session, event_id, decision.usage)
                event.status = "ANSWERED"
            else:
                create_ticket(session, event, reason)
                record_usage(session, event_id, decision.usage)
                event.status = "HANDOFF"

            event.finished_at = now()
            session.flush()
            result = self._result(session, event_id)

        self._log(result)
        return result

    def _result(self, session, event_id):
        event = session.get(Event, event_id)
        if event is None:
            raise ValueError("Stored event does not exist")

        answer = session.scalar(select(Answer).where(Answer.event_id == event_id))
        ticket = session.scalar(select(Ticket).where(Ticket.event_id == event_id))
        usage = session.scalar(
            select(ModelUsage)
            .where(ModelUsage.event_id == event_id)
            .order_by(ModelUsage.id.desc())
            .limit(1)
        )
        reason = event.last_error_code
        if answer:
            reason = (answer.validation_summary or {}).get("reason_code")
        elif ticket:
            reason = ticket.reason_code or ticket.reason

        return EventProcessingResult(
            event_id=event_id,
            status=event.status,
            decision="ANSWER" if answer else "HANDOFF" if ticket else None,
            reason_code=reason,
            reply_text=answer.reply_text
            if answer
            else insufficient_evidence_message()
            if ticket
            else None,
            citations=[Citation(**item) for item in answer.source_chunks] if answer else [],
            ticket_id=ticket.id if ticket else None,
            model_usage=Usage(
                provider=usage.provider,
                model=usage.model,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                request_id=usage.request_id,
            )
            if usage
            else None,
        )

    @staticmethod
    def _log(result):
        logger.info(
            "event_id_internal=%s status=%s decision=%s reason_code=%s",
            result.event_id,
            result.status,
            result.decision,
            result.reason_code,
        )
