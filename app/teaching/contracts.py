"""Server-owned extractive teaching plans; no model-written instructional facts."""

from dataclasses import dataclass
from enum import StrEnum

from app.generation.contracts import ReasonCode
from app.teaching.coverage import ClaimCoverage


class PresentationIntent(StrEnum):
    EXPLAIN = "EXPLAIN"
    HOWTO = "HOWTO"
    TROUBLESHOOT = "TROUBLESHOOT"
    EXAMPLE = "EXAMPLE"
    COMPARE = "COMPARE"
    POLICY = "POLICY"


@dataclass(frozen=True)
class TeachingPlan:
    intent: PresentationIntent
    coverage: ClaimCoverage
    claim_order: tuple[int, ...]
    reason: ReasonCode
