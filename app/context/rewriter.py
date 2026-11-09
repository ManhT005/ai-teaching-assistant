"""Build retrieval queries without an LLM or factual paraphrasing."""

import re

from app.context.classifier import classify, is_elliptical
from app.context.contracts import (
    ContextTurn,
    QueryResolution,
    ResolutionReason,
    ResolutionStatus,
    TeachingIntent,
)
from app.context.validation import validate_rewritten_query


def resolve_query(event_id: int, question: str, turns: tuple[ContextTurn, ...]) -> QueryResolution:
    intent = classify(question)
    if intent == TeachingIntent.STANDALONE:
        return QueryResolution(
            event_id,
            ResolutionStatus.STANDALONE,
            intent,
            question,
            (),
            ResolutionReason.INDEPENDENT_QUESTION,
        )
    if intent == TeachingIntent.NEW_TOPIC:
        return QueryResolution(
            event_id,
            ResolutionStatus.STANDALONE,
            intent,
            question,
            (),
            ResolutionReason.EXPLICIT_NEW_TOPIC,
        )
    if not is_elliptical(question):
        return QueryResolution(
            event_id,
            ResolutionStatus.STANDALONE,
            intent,
            question,
            (),
            ResolutionReason.INDEPENDENT_QUESTION,
        )
    if not turns:
        return QueryResolution(
            event_id,
            ResolutionStatus.NO_CONTEXT,
            intent,
            question,
            (),
            ResolutionReason.NO_ELIGIBLE_HISTORY,
        )
    generic_reference = any(
        marker in question.casefold()
        for marker in ("cái đó", "cai do", "nó thì sao", "no thi sao", "trường hợp đó")
    )
    if generic_reference and len(turns) > 1:
        return QueryResolution(
            event_id,
            ResolutionStatus.AMBIGUOUS,
            intent,
            question,
            (),
            ResolutionReason.AMBIGUOUS_REFERENT,
        )

    prior = turns[-1]
    # Keep only stable topic identifiers from the prior question. Historical
    # status codes and descriptive claims must not become current evidence terms.
    topic_terms = re.findall(
        r"\b(?:[A-Z][A-Z0-9_-]{1,}|[a-zA-Z]+\d[a-zA-Z0-9_-]*)\b", prior.question
    )
    topic = " ".join(dict.fromkeys(topic_terms))
    query = f"{topic} {question}"[:700].strip()
    try:
        validate_rewritten_query(question, query)
    except ValueError:
        return QueryResolution(
            event_id,
            ResolutionStatus.INVALID,
            intent,
            question,
            (),
            ResolutionReason.AMBIGUOUS_REFERENT,
        )
    return QueryResolution(
        event_id,
        ResolutionStatus.RESOLVED,
        intent,
        query,
        (prior.event_id,),
        ResolutionReason.UNIQUE_RECENT_REFERENT,
    )
