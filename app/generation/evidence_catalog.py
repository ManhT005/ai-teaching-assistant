"""Request-local quote identity; only the gate's exact allowlisted text is exposed."""

from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import ValidationError

from app.generation.contracts import EvidenceQuote
from app.generation.evidence import EvidenceDecision, paragraph_units
from app.retrieval.service import RetrievedChunk

MAX_CATALOG_QUOTES = 8


class CatalogError(ValueError):
    """Evidence identities or quote boundaries cannot be verified."""


@dataclass(frozen=True)
class EvidenceCatalog:
    quotes: tuple[EvidenceQuote, ...]

    def by_id(self) -> dict[str, EvidenceQuote]:
        return {quote.quote_id: quote for quote in self.quotes}

    def provider_evidence(self) -> dict[str, tuple[str, ...]]:
        grouped: dict[str, list[str]] = {}
        for quote in self.quotes:
            grouped.setdefault(quote.chunk_id, []).append(quote.text)
        return {chunk_id: tuple(units) for chunk_id, units in grouped.items()}


def catalog_from_units(evidence: Mapping[str, tuple[str, ...]]) -> EvidenceCatalog:
    """Provider-side numbering, after pipeline canonicalizes source/paragraph order.

    This helper does not authorize evidence. The validator always builds its catalog
    from the complete EvidenceDecision with hit identity and exact source text.
    """
    quotes = []
    for chunk_id, units in evidence.items():
        if not units:
            raise CatalogError("Empty evidence entry")
        # Repeated identical paragraphs in one chunk have one quote identity.
        for text in dict.fromkeys(units):
            if len(quotes) >= MAX_CATALOG_QUOTES:
                raise CatalogError("Too many evidence quotes")
            try:
                quote = EvidenceQuote(
                    quote_id=f"q{len(quotes) + 1:03d}",
                    chunk_id=chunk_id,
                    text=text,
                )
            except ValidationError:
                raise CatalogError("Invalid quote boundaries") from None
            quotes.append(quote)

    return EvidenceCatalog(tuple(quotes))


def build_catalog(evidence: EvidenceDecision) -> EvidenceCatalog:
    """Verify gate allowlist identity and reproduce source order without DB access."""
    if not evidence.sufficient:
        raise CatalogError("Evidence was not accepted by the gate")

    fields = set(RetrievedChunk.model_fields)
    hits = {}
    for hit in evidence.hits:
        previous = hits.get(hit.chunk_id)
        if previous is not None:
            if previous.model_dump(include=fields) != hit.model_dump(include=fields):
                raise CatalogError("Conflicting chunk identity")
        else:
            hits[hit.chunk_id] = hit

    if not evidence.evidence or set(evidence.evidence) != set(hits):
        raise CatalogError("Evidence and source identities disagree")

    ordered = {}
    for chunk_id, hit in hits.items():
        allowed = evidence.evidence[chunk_id]
        source_units = paragraph_units(hit.content)
        if not allowed or any(unit not in source_units for unit in allowed):
            raise CatalogError("Quote does not match a complete source paragraph")
        ordered[chunk_id] = tuple(unit for unit in source_units if unit in allowed)

    return catalog_from_units(ordered)
