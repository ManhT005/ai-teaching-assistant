"""Pure composition from validated quote identities; model prose is never used."""

from app.generation.contracts import AnswerDraft, AnswerSelection, Claim
from app.generation.evidence_catalog import EvidenceCatalog


def compose_validated_answer(selection: AnswerSelection, catalog: EvidenceCatalog) -> AnswerDraft:
    ids = selection.quote_ids
    if selection.needs_handoff or not 1 <= len(ids) <= 3 or len(set(ids)) != len(ids):
        raise ValueError("Selection cannot form an answer")

    by_id = catalog.by_id()
    if any(quote_id not in by_id for quote_id in ids):
        raise ValueError("Selection is outside the catalog")

    claims = [
        Claim(text=by_id[quote_id].text, citation_ids=[by_id[quote_id].chunk_id])
        for quote_id in ids
    ]
    # Pydantic enforces the 6,000 character budget, including newline separators.
    # No paragraph, qualification or negation is truncated to fit that budget.
    return AnswerDraft(
        answer_text="\n".join(claim.text for claim in claims),
        claims=claims,
        needs_handoff=False,
    )
