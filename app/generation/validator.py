"""Validate ID-only model output and compose exclusively from authorized quotes."""

from pydantic import ValidationError

from app.generation.composer import compose_validated_answer
from app.generation.contracts import AnswerDraft, AnswerSelection, ReasonCode
from app.generation.evidence import EvidenceDecision
from app.generation.evidence_catalog import CatalogError, build_catalog
from app.generation.safety import is_sensitive_policy, is_unsafe


def validate_answer(raw: str, evidence: EvidenceDecision) -> tuple[ReasonCode, AnswerDraft | None]:
    try:
        selection = AnswerSelection.model_validate_json(raw)
    except ValidationError:
        return ReasonCode.UNSUPPORTED_CLAIM, None

    if selection.needs_handoff:
        if selection.quote_ids:
            return ReasonCode.UNSUPPORTED_CLAIM, None
        return ReasonCode.INCOMPLETE_EVIDENCE, None
    if not selection.quote_ids:
        return ReasonCode.UNSUPPORTED_CLAIM, None

    try:
        catalog = build_catalog(evidence)
    except CatalogError:
        return ReasonCode.INVALID_CITATION, None

    by_id = catalog.by_id()
    if len(set(selection.quote_ids)) != len(selection.quote_ids) or any(
        quote_id not in by_id for quote_id in selection.quote_ids
    ):
        return ReasonCode.INVALID_CITATION, None

    for quote_id in selection.quote_ids:
        text = by_id[quote_id].text
        if is_unsafe(text):
            return ReasonCode.UNSAFE_RESPONSE, None
        if is_sensitive_policy(text):
            return ReasonCode.SENSITIVE_OR_POLICY, None

    try:
        draft = compose_validated_answer(selection, catalog)
    except ValueError:
        return ReasonCode.UNSUPPORTED_CLAIM, None

    return ReasonCode.GROUNDED_ANSWER, draft
