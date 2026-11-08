"""Retrieve, gate, generate and validate without persistence or delivery."""

from dataclasses import dataclass

from app.generation.contracts import AnswerDraft, ReasonCode, Usage
from app.generation.evidence import EvidenceDecision, EvidenceGate
from app.generation.evidence_catalog import CatalogError, build_catalog
from app.generation.validator import validate_answer
from app.llm.provider import LLMProvider
from app.retrieval.service import normalize_question


@dataclass(frozen=True)
class PipelineDecision:
    reason: ReasonCode
    evidence: EvidenceDecision
    draft: AnswerDraft | None = None
    usage: Usage | None = None


class AnswerPipeline:
    def __init__(self, retrieval, gate: EvidenceGate, provider: LLMProvider):
        self.retrieval = retrieval
        self.gate = gate
        self.provider = provider

    def run(self, question, scope) -> PipelineDecision:
        question = normalize_question(question)
        retrieved = self.retrieval.search(question, scope, top_k=3)
        evidence = self.gate.assess(question, scope, retrieved)

        if not evidence.sufficient:
            return PipelineDecision(evidence.reason, evidence)

        try:
            # Canonical copy gives provider and validator identical numbering,
            # without giving the provider a mutable reference to the gate allowlist.
            catalog = build_catalog(evidence)
        except CatalogError:
            return PipelineDecision(ReasonCode.INVALID_CITATION, evidence)

        generated = self.provider.generate(question, catalog.provider_evidence())
        reason, draft = validate_answer(generated.text, evidence)
        return PipelineDecision(reason, evidence, draft, generated.usage)
