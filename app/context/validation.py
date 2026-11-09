"""Validation for bounded deterministic retrieval query construction."""

import re

from app.generation.safety import is_unsafe


def validate_rewritten_query(original: str, rewritten: str) -> None:
    if not rewritten or len(rewritten) > 700 or is_unsafe(rewritten):
        raise ValueError("Rewritten query is invalid")
    if not original.strip() or original.casefold() not in rewritten.casefold():
        raise ValueError("Current question must remain present in the retrieval query")
    identifiers = re.findall(r"[A-Za-z0-9_./:-]+", original)
    if any(token.casefold() not in rewritten.casefold() for token in identifiers):
        raise ValueError("Explicit terms from the current question must be preserved")
