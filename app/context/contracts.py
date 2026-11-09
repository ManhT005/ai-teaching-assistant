"""Strict internal contracts for ephemeral conversation context."""

from dataclasses import dataclass
from enum import StrEnum


class ResolutionStatus(StrEnum):
    STANDALONE = "STANDALONE"
    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    NO_CONTEXT = "NO_CONTEXT"
    INVALID = "INVALID"


class TeachingIntent(StrEnum):
    STANDALONE = "STANDALONE"
    FOLLOWUP_EXPLANATION = "FOLLOWUP_EXPLANATION"
    FOLLOWUP_EXAMPLE = "FOLLOWUP_EXAMPLE"
    FOLLOWUP_REFERENCE = "FOLLOWUP_REFERENCE"
    NEW_TOPIC = "NEW_TOPIC"


class ResolutionReason(StrEnum):
    INDEPENDENT_QUESTION = "INDEPENDENT_QUESTION"
    EXPLICIT_NEW_TOPIC = "EXPLICIT_NEW_TOPIC"
    UNIQUE_RECENT_REFERENT = "UNIQUE_RECENT_REFERENT"
    NO_ELIGIBLE_HISTORY = "NO_ELIGIBLE_HISTORY"
    AMBIGUOUS_REFERENT = "AMBIGUOUS_REFERENT"
    UNSAFE_INPUT = "UNSAFE_INPUT"
    QUERY_TOO_LONG = "QUERY_TOO_LONG"


@dataclass(frozen=True)
class ContextTurn:
    event_id: int
    question: str


@dataclass(frozen=True)
class QueryResolution:
    event_id: int
    status: ResolutionStatus
    intent: TeachingIntent
    standalone_question: str
    antecedent_event_ids: tuple[int, ...]
    reason: ResolutionReason
    revision: str = "context-rewrite-v2"
