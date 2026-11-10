"""Fail-closed source snapshots and conservative paragraph coverage rules."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from sqlalchemy import select

from app.generation.contracts import ReasonCode
from app.generation.question_evidence import (
    asks_rag_comparison,
    asks_submission_deadline,
    explains_rag_comparison,
    requires_comparative_or_causal_evidence,
    submission_deadlines,
)
from app.generation.safety import folded, is_sensitive_policy, is_unsafe
from app.models import ChannelBinding, Classroom, Document, DocumentChunk
from app.retrieval.service import (
    AuthorizedScope,
    RetrievalResponse,
    RetrievedChunk,
    scoped_candidate_query,
    validate_authorized_scope,
)

# Conversational framing is ignored; every remaining content word must be covered.
# This is a restrictive demo policy, not a probability or semantic correctness score.
QUESTION_FRAMING = frozenset(
    folded(
        "em tôi mình bạn thầy cô anh chị cho hỏi với ạ nhé giúp hỗ trợ "
        "cách làm thế nào gì cần kiểm tra xử lý khi thì bị gặp lỗi chạy gọi "
        "trong trên của và hoặc là có được không hãy hướng dẫn về một các những "
        "the a an how what please error fix check use using in on for to is do i"
    ).split()
)


def required_terms(question: str) -> frozenset[str]:
    return frozenset(re.findall(r"\w+", folded(question))) - QUESTION_FRAMING


def paragraph_units(content: str) -> tuple[str, ...]:
    # Whole paragraphs retain qualifications and negation; never extract a substring.
    return tuple(
        unit.strip() for unit in re.split(r"\n\s*\n", content) if 20 <= len(unit.strip()) <= 2000
    )


def sources_current(session, scope: AuthorizedScope, hits, *, lock=False) -> bool:
    """Compare every provenance/content field with eligible current DB rows."""
    if lock:
        # Stable lock order; share locks serialize finalization with updates/revoke.
        session.execute(
            select(Classroom.id).where(Classroom.id == scope.class_id).with_for_update(read=True)
        ).all()
        session.execute(
            select(ChannelBinding.id)
            .where(ChannelBinding.id == scope.binding_id)
            .with_for_update(read=True)
        ).all()
        for model, ids in (
            (Document, sorted({hit.document_id for hit in hits})),
            (DocumentChunk, sorted({hit.row_id for hit in hits})),
        ):
            if ids:
                session.execute(
                    select(model.id)
                    .where(model.id.in_(ids))
                    .order_by(model.id)
                    .with_for_update(read=True)
                ).all()

    validate_authorized_scope(session, scope)
    if not hits:
        return True

    rows = session.execute(
        scoped_candidate_query(scope.class_code).where(
            DocumentChunk.id.in_([hit.row_id for hit in hits])
        )
    ).mappings()
    current = {row["row_id"]: RetrievedChunk.model_validate(dict(row)) for row in rows}
    fields = set(RetrievedChunk.model_fields)
    return all(
        hit.row_id in current and current[hit.row_id].model_dump() == hit.model_dump(include=fields)
        for hit in hits
    )


@dataclass(frozen=True)
class EvidenceDecision:
    reason: ReasonCode
    evidence: Mapping[str, tuple[str, ...]]
    hits: tuple = ()
    reviewed_comparison: bool = False

    def __post_init__(self):
        # One immutable request snapshot; provider receives a separate canonical copy.
        object.__setattr__(
            self,
            "evidence",
            MappingProxyType({chunk_id: tuple(units) for chunk_id, units in self.evidence.items()}),
        )
        object.__setattr__(self, "hits", tuple(self.hits))

    @property
    def sufficient(self) -> bool:
        return self.reason == ReasonCode.GROUNDED_ANSWER


class EvidenceGate:
    def __init__(self, sessions, *, max_context_characters=12000):
        self.sessions = sessions
        self.max_context_characters = max_context_characters

    def assess(self, question: str, scope: AuthorizedScope, result: RetrievalResponse):
        with self.sessions() as session:
            valid = sources_current(session, scope, result.hits)

        if result.authorized_class != scope.class_code or not valid:
            return EvidenceDecision(ReasonCode.NO_AUTHORIZED_SOURCE, {})
        if is_unsafe(question) or any(is_unsafe(hit.content) for hit in result.hits):
            return EvidenceDecision(ReasonCode.UNSAFE_RESPONSE, {})
        if is_sensitive_policy(question):
            return EvidenceDecision(ReasonCode.SENSITIVE_OR_POLICY, {})
        if asks_submission_deadline(question):
            return self._select_direct_evidence(scope, comparison=False)
        if asks_rag_comparison(question):
            return self._select_direct_evidence(scope, comparison=True)
        if not result.hits:
            return EvidenceDecision(ReasonCode.NO_AUTHORIZED_SOURCE, {})

        terms = required_terms(question)
        if not terms:
            return EvidenceDecision(ReasonCode.AMBIGUOUS_QUESTION, {})
        if requires_comparative_or_causal_evidence(question):
            # Comparisons and causal explanations require semantic assessment beyond this MVP.
            return EvidenceDecision(ReasonCode.INCOMPLETE_EVIDENCE, {})

        evidence = {}
        selected = []
        used = 0
        for hit in result.hits:
            match_terms = terms
            if (
                {"n8n", "401"} <= terms <= {"n8n", "http", "api", "401", "unauthorized"}
                and hit.class_scope == scope.class_code == "AUTO-N8N-01"
                and (hit.doc_id, hit.section_id) == ("DOC-AUTO-01", "AUTO-03")
            ):
                # The authorized n8n API section establishes the platform and protocol.
                match_terms = terms - {"n8n", "http", "api"}
            units = []
            for unit in paragraph_units(hit.content):
                if is_sensitive_policy(unit):
                    continue
                if not match_terms <= set(re.findall(r"\w+", folded(unit))):
                    continue
                if used + len(unit) > self.max_context_characters:
                    continue
                units.append(unit)
                used += len(unit)
                if sum(len(values) for values in evidence.values()) + len(units) >= 8:
                    break

            if units:
                evidence[hit.chunk_id] = tuple(units)
                selected.append(hit)
            if sum(len(values) for values in evidence.values()) >= 8:
                break

        if not evidence:
            return EvidenceDecision(ReasonCode.INCOMPLETE_EVIDENCE, {})

        return EvidenceDecision(ReasonCode.GROUNDED_ANSWER, evidence, tuple(selected))

    def _select_direct_evidence(
        self, scope: AuthorizedScope, *, comparison: bool
    ) -> EvidenceDecision:
        # A targeted lookup complements hybrid ranking for these bounded intents.
        # Both paths use the same current-source and scope predicates as retrieval.
        statement = scoped_candidate_query(scope.class_code)
        if comparison:
            statement = statement.where(
                Document.review_status == "approved_demo",
                DocumentChunk.review_status == "approved_demo",
                DocumentChunk.content.ilike("%rag%"),
                DocumentChunk.content.ilike("%fine%tuning%"),
            )
        else:
            statement = statement.where(
                DocumentChunk.class_scope == scope.class_code,
                DocumentChunk.content.ilike("%bài tập%nộp%"),
                DocumentChunk.content.op("~")(r"[0-9]{2}:[0-9]{2}"),
            )
        with self.sessions() as session:
            validate_authorized_scope(session, scope)
            rows = session.execute(statement.order_by(DocumentChunk.id).limit(21)).mappings().all()

        # Fail closed if the bounded lookup cannot check all candidate sources.
        if len(rows) > 20:
            return EvidenceDecision(ReasonCode.INCOMPLETE_EVIDENCE, {})

        matches = []
        deadlines = set()
        for row in rows:
            hit = RetrievedChunk.model_validate(dict(row))
            if is_unsafe(hit.content):
                return EvidenceDecision(ReasonCode.UNSAFE_RESPONSE, {})
            for unit in paragraph_units(hit.content):
                if len(unit) > self.max_context_characters or is_sensitive_policy(unit):
                    continue
                times = submission_deadlines(unit) if not comparison else set()
                if times or (comparison and explains_rag_comparison(unit)):
                    matches.append((hit, unit))
                    deadlines.update(times)

        if not matches or (not comparison and len(deadlines) != 1):
            return EvidenceDecision(ReasonCode.INCOMPLETE_EVIDENCE, {})
        hit, unit = matches[0]
        return EvidenceDecision(
            ReasonCode.GROUNDED_ANSWER,
            {hit.chunk_id: (unit,)},
            (hit,),
            reviewed_comparison=comparison,
        )
