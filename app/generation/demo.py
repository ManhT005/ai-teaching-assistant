"""Legacy parser-only evidence helper; event orchestration lives in pipeline.py."""

from dataclasses import dataclass

from app.knowledge.ingestion import KnowledgeChunk, eligible_demo_chunks


@dataclass(frozen=True)
class EvidenceDecision:
    action: str
    reason: str
    sources: tuple[KnowledgeChunk, ...] = ()


def evaluate_demo_evidence(
    chunks: list[KnowledgeChunk],
    *,
    authorized_class: str,
    required_topics: set[str],
    relevance_verified: bool = False,
) -> EvidenceDecision:
    """Caller supplies verified requirements and relevance; topic labels alone are insufficient.

    This is not a semantic evaluator and does not mark the mandatory comparison case passed.
    Empty requirements or absent relevance assessment fail closed.
    """
    allowed = eligible_demo_chunks(chunks, authorized_class)
    if not allowed:
        return EvidenceDecision("handoff", "NO_AUTHORIZED_REVIEWED_SOURCE")
    coverage = {topic for chunk in allowed for topic in chunk.topics}
    if not required_topics or not required_topics.issubset(coverage):
        return EvidenceDecision("handoff", "INCOMPLETE_EVIDENCE")
    if not relevance_verified:
        return EvidenceDecision("handoff", "RELEVANCE_NOT_VERIFIED")
    return EvidenceDecision("answer", "EVIDENCE_CHECKED", tuple(allowed))
