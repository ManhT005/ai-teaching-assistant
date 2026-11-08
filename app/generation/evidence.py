"""Fail-closed source snapshots and conservative paragraph coverage rules."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from sqlalchemy import select

from app.generation.contracts import ReasonCode
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
        "cách làm thế nào gì cần kiểm tra xử lý khi bị gặp lỗi chạy gọi "
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
        if not result.hits:
            return EvidenceDecision(ReasonCode.NO_AUTHORIZED_SOURCE, {})

        terms = required_terms(question)
        if not terms:
            return EvidenceDecision(ReasonCode.AMBIGUOUS_QUESTION, {})
        if re.search(r"so sanh|khac|versus|\bvs\b|fine.?tuning|tai sao|why", folded(question)):
            # Comparisons and causal explanations require semantic assessment beyond this MVP.
            return EvidenceDecision(ReasonCode.INCOMPLETE_EVIDENCE, {})

        evidence = {}
        selected = []
        used = 0
        for hit in result.hits:
            units = []
            for unit in paragraph_units(hit.content):
                if is_sensitive_policy(unit):
                    continue
                if not terms <= set(re.findall(r"\w+", folded(unit))):
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
