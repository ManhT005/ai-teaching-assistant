"""Conservative deterministic follow-up classification."""

import re

from app.context.contracts import TeachingIntent
from app.generation.safety import folded

_NEW_TOPIC = re.compile(r"\b(doi voi chu de khac|chuyen sang|cau hoi khac|new topic)\b")
_EXAMPLE = re.compile(r"\b(cho em|cho minh|cho toi)?\s*(vi du|example)\b")
_EXPLAIN = re.compile(r"\b(tai sao|vi sao|giai thich|noi ro hon|explain|why)\b")
_REQUEST_FRAMING = re.compile(
    r"\b(cho|em|minh|toi|ban|mot|vai|vi du|example|examples|ve|cua|cach|"
    r"tai sao|vi sao|giai thich|noi ro hon|ky hon|explain|why|please|about|of|an)\b"
)
_REFERENT = re.compile(r"\b(cai do|truong hop do|no|do|nay|that|it|this)\b")


def _has_explicit_subject(value: str) -> bool:
    remaining = _REQUEST_FRAMING.sub(" ", value)
    remaining = re.sub(r"[^\w]+", " ", remaining).strip()
    return bool(remaining) and not _REFERENT.search(remaining)


_ELLIPTICAL = re.compile(
    r"^(con\b|cai do\b|truong hop do\b|the con\b|vi du\b|cho em vi du\b|"
    r"giai thich ky hon\b|tai sao\??$|vi sao\??$|no thi sao\b|what about\b)"
)


def classify(question: str) -> TeachingIntent:
    value = folded(question).strip(" ?!.,")
    if _NEW_TOPIC.search(value):
        return TeachingIntent.NEW_TOPIC
    if _EXAMPLE.search(value):
        return (
            TeachingIntent.STANDALONE
            if _has_explicit_subject(value)
            else TeachingIntent.FOLLOWUP_EXAMPLE
        )
    if _EXPLAIN.search(value):
        return (
            TeachingIntent.STANDALONE
            if _has_explicit_subject(value)
            else TeachingIntent.FOLLOWUP_EXPLANATION
        )
    if _ELLIPTICAL.search(value):
        return TeachingIntent.FOLLOWUP_REFERENCE
    return TeachingIntent.STANDALONE


def is_elliptical(question: str) -> bool:
    return classify(question) != TeachingIntent.STANDALONE and not _NEW_TOPIC.search(
        folded(question)
    )
