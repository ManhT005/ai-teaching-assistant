"""Claim stored events, release DB connections for generation, persist one outcome."""

import logging
from datetime import datetime

from sqlalchemy import func, select

from app.config import get_settings
from app.context.contracts import ResolutionReason, ResolutionStatus
from app.context.resolver import ContextResolver
from app.delivery.outbox import create_outbox
from app.events.messages import handoff_acknowledgment, insufficient_evidence_message
from app.events.queue import ClaimedEvent, EventQueue
from app.events.retry import RetryPolicy
from app.events.tickets import create_ticket, record_usage
from app.generation.contracts import Citation, EventProcessingResult, ReasonCode, Usage
from app.generation.evidence import EvidenceDecision, sources_current
from app.generation.pipeline import PipelineDecision
from app.generation.validator import validate_composed_answer
from app.llm.provider import ProviderFailure
from app.mock_channel import question_from_text
from app.models import Answer, Event, ModelUsage, Ticket
from app.retrieval.service import (
    ScopeAuthorizationError,
    normalize_question,
    resolve_authorized_scope,
)
from app.teaching.formatter import format_planned_reply, format_teaching_reply

logger = logging.getLogger(__name__)


class EventProcessor:
    def __init__(self, sessions, pipeline):
        self.sessions = sessions
        self.pipeline = pipeline
        settings = get_settings()
        self.context_resolver = ContextResolver(
            sessions,
            ttl_minutes=settings.context_ttl_minutes,
            max_turns=settings.context_max_turns,
            max_characters=settings.context_max_characters,
        )
        self.queue = EventQueue(
            sessions,
            lease_seconds=settings.event_lease_seconds,
            max_attempts=settings.event_max_attempts,
        )
        self.retry_policy = RetryPolicy(
            base_seconds=settings.event_backoff_base_seconds,
            cap_seconds=settings.event_backoff_cap_seconds,
        )

    def process_event(self, event_id: int) -> EventProcessingResult:
        if type(event_id) is not int or event_id < 1:
            raise ValueError("Use a positive internal event ID")

        claim = self.queue.claim_event(event_id)
        if claim is None:
            with self.sessions() as session:
                return self._result(session, event_id)

        return self.process_claim(claim)

    def process_claim(self, claim: ClaimedEvent) -> EventProcessingResult:
        event_id = claim.event_id

        try:
            if not self.queue.is_current(claim):
                return self._current_result(claim.event_id)
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
                return self._failure(claim, ReasonCode.INVALID_EVENT, retryable=False)

            resolution = self.context_resolver.resolve(event_id, question, scope)
            if resolution.status in {
                ResolutionStatus.AMBIGUOUS,
                ResolutionStatus.NO_CONTEXT,
                ResolutionStatus.INVALID,
            }:
                decision = PipelineDecision(
                    reason=(
                        ReasonCode.UNSAFE_RESPONSE
                        if resolution.reason == ResolutionReason.UNSAFE_INPUT
                        else ReasonCode.AMBIGUOUS_QUESTION
                    ),
                    evidence=EvidenceDecision(
                        ReasonCode.UNSAFE_RESPONSE
                        if resolution.reason == ResolutionReason.UNSAFE_INPUT
                        else ReasonCode.AMBIGUOUS_QUESTION,
                        {},
                    ),
                )
            else:
                decision = self.pipeline.run(resolution.standalone_question, scope)
            return self._persist(claim, scope, decision, teaching_intent=resolution.intent)

        except ScopeAuthorizationError:
            return self._failure(claim, ReasonCode.SCOPE_REVOKED, retryable=False)
        except ProviderFailure as error:
            return self._failure(claim, ReasonCode.PROVIDER_FAILURE, retryable=error.retryable)
        except Exception:  # noqa: BLE001 -- fail closed without exposing exception contents
            # Never log provider bodies, raw SQL parameters, prompts, event text or keys.
            return self._failure(claim, ReasonCode.PROCESSING_FAILURE, retryable=True)

    def _failure(self, claim: ClaimedEvent, reason, *, retryable):
        with self.sessions.begin() as session:
            event = session.get(Event, claim.event_id, with_for_update=True)
            database_now = session.scalar(select(func.now()))
            if self._owns_lease(event, claim, database_now):
                retry = retryable and claim.attempts < self.queue.max_attempts
                event.status = "FAILED_RETRYABLE" if retry else "FAILED_FINAL"
                event.last_error_code = (
                    reason.value if retry else ("RETRIES_EXHAUSTED" if retryable else reason.value)
                )
                event.error_category = self._error_category(reason)
                event.next_attempt_at = (
                    self.retry_policy.next_attempt_at(claim.attempts, now=database_now)
                    if retry
                    else None
                )
                event.finished_at = None if retry else database_now
                event.lease_token = None
                event.lease_expires_at = None
                event.worker_id = None
            result = self._result(session, claim.event_id)

        self._log(result)
        return result

    def _persist(self, claim: ClaimedEvent, scope, decision, *, teaching_intent=None):
        event_id = claim.event_id
        with self.sessions.begin() as session:
            event = session.get(Event, event_id, with_for_update=True)
            database_now = session.scalar(select(func.now()))
            if not self._owns_lease(event, claim, database_now):
                return self._result(session, event_id)

            if not self.context_resolver.snapshot_is_current(session, event_id, scope):
                decision = PipelineDecision(
                    reason=ReasonCode.AMBIGUOUS_QUESTION,
                    evidence=EvidenceDecision(ReasonCode.AMBIGUOUS_QUESTION, {}),
                    usage=decision.usage,
                )

            reason = decision.reason
            draft = decision.draft
            if draft is not None:
                checked_reason = validate_composed_answer(draft, decision.evidence)
                if checked_reason != ReasonCode.GROUNDED_ANSWER:
                    reason, draft = checked_reason, None
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
                event.error_category = "authorization"
            elif draft is not None and reason == ReasonCode.GROUNDED_ANSWER:
                cited_ids = list(
                    dict.fromkeys(
                        chunk_id for claim in draft.claims for chunk_id in claim.citation_ids
                    )
                )
                by_chunk = {hit.chunk_id: hit for hit in decision.evidence.hits}
                citations = [
                    Citation(
                        chunk_id=chunk_id,
                        doc_id=by_chunk[chunk_id].doc_id,
                        document_version=by_chunk[chunk_id].version,
                        section_id=by_chunk[chunk_id].section_id,
                        source_name=by_chunk[chunk_id].source_name,
                        locator=by_chunk[chunk_id].locator,
                        class_scope=by_chunk[chunk_id].class_scope,
                    )
                    for chunk_id in cited_ids
                ]
                numbers = {citation.chunk_id: i for i, citation in enumerate(citations, start=1)}
                plan = decision.teaching_plan
                claims = (
                    [draft.claims[index] for index in plan.claim_order]
                    if plan is not None
                    else draft.claims
                )
                reply = "\n".join(
                    f"{index}. {claim.text} "
                    + " ".join(f"[{numbers[chunk_id]}]" for chunk_id in claim.citation_ids)
                    for index, claim in enumerate(claims, start=1)
                )
                if plan is not None:
                    reply = format_planned_reply(plan.intent, reply)
                elif teaching_intent is not None:
                    reply = format_teaching_reply(teaching_intent, reply)
                session.add(
                    Answer(
                        event_id=event_id,
                        decision="ANSWER",
                        reply_text=reply,
                        source_chunks=[citation.model_dump() for citation in citations],
                        validation_summary={
                            "reason_code": reason.value,
                            "policy": "quote-selection-v1.1",
                            "claims_validated": len(draft.claims),
                            "teaching_intent": plan.intent.value if plan else None,
                            "claim_coverage": plan.coverage.value if plan else None,
                        },
                        provider_request_id=decision.usage.request_id if decision.usage else None,
                    )
                )
                record_usage(session, event_id, decision.usage)
                create_outbox(
                    session,
                    event,
                    kind="ANSWER_ACK",
                    body=reply,
                    citations=[citation.model_dump(mode="json") for citation in citations],
                )
                event.status = "ANSWERED"
            else:
                ticket = create_ticket(session, event, reason)
                if decision.teaching_plan is not None:
                    ticket.evidence_summary = {
                        **(ticket.evidence_summary or {}),
                        "teaching_intent": decision.teaching_plan.intent.value,
                        "claim_coverage": decision.teaching_plan.coverage.value,
                    }
                record_usage(session, event_id, decision.usage)
                create_outbox(
                    session,
                    event,
                    kind="HANDOFF_ACK",
                    body=handoff_acknowledgment(),
                    citations=[],
                )
                event.status = "HANDOFF"

            event.finished_at = database_now
            event.lease_token = None
            event.lease_expires_at = None
            event.worker_id = None
            event.next_attempt_at = None
            session.flush()
            result = self._result(session, event_id)

        self._log(result)
        return result

    @staticmethod
    def _owns_lease(event: Event | None, claim: ClaimedEvent, database_now: datetime) -> bool:
        return bool(
            event
            and event.status == "PROCESSING"
            and event.lease_token == claim.lease_token
            and event.lease_expires_at is not None
            and event.lease_expires_at > database_now
        )

    def _current_result(self, event_id: int):
        with self.sessions() as session:
            return self._result(session, event_id)

    @staticmethod
    def _error_category(reason: ReasonCode) -> str:
        if reason == ReasonCode.PROVIDER_FAILURE:
            return "provider"
        if reason == ReasonCode.SCOPE_REVOKED:
            return "authorization"
        if reason == ReasonCode.INVALID_EVENT:
            return "invalid_input"
        return "processing"

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
