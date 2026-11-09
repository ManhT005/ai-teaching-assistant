"""Plan presentation only after exact quote validation and fresh retrieval."""

import re

from app.generation.contracts import AnswerDraft, ReasonCode
from app.generation.evidence import EvidenceDecision
from app.generation.safety import folded, is_sensitive_policy
from app.teaching.contracts import PresentationIntent, TeachingPlan
from app.teaching.coverage import ClaimCoverage, assess_claim_coverage

_EXAMPLE = re.compile(r"\b(vi du|example|examples)\b|\be\.g\.")


def teaching_intent(question: str) -> PresentationIntent:
    value = folded(question)
    if is_sensitive_policy(question):
        return PresentationIntent.POLICY
    if re.search(r"so sanh|khac|versus|\bvs\b|compare", value):
        return PresentationIntent.COMPARE
    if _EXAMPLE.search(value):
        return PresentationIntent.EXAMPLE
    if re.search(r"\b(loi|error|401|403|troubleshoot|khong chay)\b", value):
        return PresentationIntent.TROUBLESHOOT
    if re.search(r"\b(cach|cac buoc|lam the nao|how to|step)\b", value):
        return PresentationIntent.HOWTO
    return PresentationIntent.EXPLAIN


def plan_teaching(question: str, draft: AnswerDraft, evidence: EvidenceDecision) -> TeachingPlan:
    intent = teaching_intent(question)
    labels = [
        assess_claim_coverage(
            (claim.text,),
            {chunk_id: evidence.evidence.get(chunk_id, ()) for chunk_id in claim.citation_ids},
        )
        for claim in draft.claims
    ]
    coverage = (
        ClaimCoverage.SUPPORTED
        if all(label == ClaimCoverage.SUPPORTED for label in labels)
        else ClaimCoverage.PARTIALLY_SUPPORTED
        if ClaimCoverage.SUPPORTED in labels
        else ClaimCoverage.UNSUPPORTED
    )
    order = tuple(range(len(draft.claims)))
    reason = ReasonCode.GROUNDED_ANSWER
    if coverage != ClaimCoverage.SUPPORTED:
        reason = ReasonCode.UNSUPPORTED_CLAIM
    elif intent == PresentationIntent.POLICY:
        reason = ReasonCode.SENSITIVE_OR_POLICY
    elif intent == PresentationIntent.COMPARE or re.search(
        r"tai sao|vi sao|\bwhy\b", folded(question)
    ):
        reason = ReasonCode.INCOMPLETE_EVIDENCE
    elif intent == PresentationIntent.EXAMPLE:
        examples = tuple(
            i for i, claim in enumerate(draft.claims) if _EXAMPLE.search(folded(claim.text))
        )
        if not examples:
            reason = ReasonCode.INCOMPLETE_EVIDENCE
        else:
            order = examples + tuple(i for i in order if i not in examples)
    return TeachingPlan(intent, coverage, order, reason)
