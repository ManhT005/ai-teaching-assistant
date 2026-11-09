"""Conservative deterministic follow-up classification."""

import re

from app.context.contracts import TeachingIntent
from app.generation.safety import folded

_NEW_TOPIC = re.compile(r"\b(doi voi chu de khac|chuyen sang|cau hoi khac|new topic)\b")
_EXAMPLE = re.compile(r"\b(cho em|cho minh|cho toi)?\s*(vi du|example)\b")
_EXPLAIN = re.compile(r"\b(tai sao|vi sao|giai thich|noi ro hon|explain|why)\b")
_ELLIPTICAL = re.compile(
    r"^(con\b|cai do\b|truong hop do\b|the con\b|vi du\b|cho em vi du\b|"
    r"giai thich ky hon\b|tai sao\??$|vi sao\??$|no thi sao\b|what about\b)"
)


def classify(question: str) -> TeachingIntent:
    value = folded(question).strip(" ?!.,")
    if _NEW_TOPIC.search(value):
        return TeachingIntent.NEW_TOPIC
    if _EXAMPLE.search(value):
        return TeachingIntent.FOLLOWUP_EXAMPLE
    if _EXPLAIN.search(value):
        return TeachingIntent.FOLLOWUP_EXPLANATION
    if _ELLIPTICAL.search(value):
        return TeachingIntent.FOLLOWUP_REFERENCE
    return TeachingIntent.STANDALONE


def is_elliptical(question: str) -> bool:
    return classify(question) != TeachingIntent.STANDALONE and not _NEW_TOPIC.search(
        folded(question)
    )
