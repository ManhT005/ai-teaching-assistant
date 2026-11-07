"""Validate every output word against complete, pre-authorized quote units."""

from pydantic import ValidationError

from app.generation.contracts import AnswerDraft, ReasonCode
from app.generation.evidence import EvidenceDecision
from app.generation.safety import is_sensitive_policy, is_unsafe


def validate_answer(raw: str, evidence: EvidenceDecision) -> tuple[ReasonCode, AnswerDraft | None]:
    try:
        draft = AnswerDraft.model_validate_json(raw)
    except ValidationError:
        return ReasonCode.UNSUPPORTED_CLAIM, None

    if is_unsafe(draft.answer_text) or any(is_unsafe(claim.text) for claim in draft.claims):
        return ReasonCode.UNSAFE_RESPONSE, None
    if is_sensitive_policy(draft.answer_text):
        return ReasonCode.SENSITIVE_OR_POLICY, None
    if draft.needs_handoff:
        return ReasonCode.INCOMPLETE_EVIDENCE, None

    for claim in draft.claims:
        if any(chunk_id not in evidence.evidence for chunk_id in claim.citation_ids):
            return ReasonCode.INVALID_CITATION, None
        if any(claim.text not in evidence.evidence[chunk_id] for chunk_id in claim.citation_ids):
            return ReasonCode.UNSUPPORTED_CLAIM, None

    if draft.answer_text != "\n".join(claim.text for claim in draft.claims):
        return ReasonCode.UNSUPPORTED_CLAIM, None
    if len({claim.text for claim in draft.claims}) != len(draft.claims):
        return ReasonCode.UNSUPPORTED_CLAIM, None

    return ReasonCode.GROUNDED_ANSWER, draft
