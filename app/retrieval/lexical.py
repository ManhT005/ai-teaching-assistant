"""Conservative technical anchors for PostgreSQL's simple text-search dictionary."""

import re
import unicodedata

from sqlalchemy import func

TECHNICAL_TERMS = frozenset(
    {
        "sumifs",
        "countifs",
        "importrange",
        "eomonth",
        "today",
        "timestamp",
        "sum_range",
        "criteria_range",
        "date",
        "text",
        "crm",
        "kpi",
        "dashboard",
        "lead",
        "n8n",
        "webhook",
        "http",
        "request",
        "requests",
        "api",
        "url",
        "endpoint",
        "production",
        "trigger",
        "schedule",
        "workflow",
        "json",
        "unauthorized",
        "forbidden",
        "authorization",
        "header",
        "token",
        "key",
        "credentials",
        "role",
        "scope",
        "backoff",
        "retry",
        "timeout",
        "secrets",
        "hardcode",
        "rag",
        "agent",
        "embedding",
        "retrieval",
        "llm",
        "chunk",
        "event_id",
        "class_id",
        "idempotency",
        "log",
        "ref",
        "comment",
        "note",
    }
)
TOKEN_PATTERN = re.compile(r"\b[a-z0-9_]+\b")
SAFE_TOKEN = re.compile(r"(?:[a-z][a-z0-9_]{0,39}|[1-5][0-9]{2})", re.ASCII)


def extract_technical_terms(question: str, max_terms: int = 6) -> tuple[str, ...]:
    if type(max_terms) is not int or not 1 <= max_terms <= 12:
        raise ValueError("Lexical token limit must be between 1 and 12")

    normalized = unicodedata.normalize("NFC", question).casefold()
    terms = []

    for token in TOKEN_PATTERN.findall(normalized):
        if token not in TECHNICAL_TERMS and not re.fullmatch(r"[1-5][0-9]{2}", token):
            continue

        if token not in terms:
            terms.append(token)

        if len(terms) == max_terms:
            break

    return tuple(terms)


def safe_or_query(terms: tuple[str, ...]) -> str:
    """Only validated positive lexemes can reach tsquery syntax; SQL stays bound."""
    if len(terms) > 12 or any(
        not isinstance(term, str) or not SAFE_TOKEN.fullmatch(term) for term in terms
    ):
        raise ValueError("Lexical query accepts at most 12 sanitized technical tokens")

    return " | ".join(dict.fromkeys(terms))


def lexical_tsquery(terms: tuple[str, ...]):
    return func.to_tsquery("simple", safe_or_query(terms))
