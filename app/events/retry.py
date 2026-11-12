"""Bounded exponential retry timing with deterministic injectable jitter."""

import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta


class RetryPolicy:
    def __init__(
        self,
        *,
        base_seconds: int = 2,
        cap_seconds: int = 60,
        jitter: Callable[[float], float] | None = None,
    ):
        if base_seconds < 1 or cap_seconds < 1:
            raise ValueError("Retry delays must be positive")
        self.base_seconds = base_seconds
        self.cap_seconds = cap_seconds
        self.jitter = jitter or (lambda ceiling: random.uniform(0.0, ceiling))

    def delay_seconds(self, attempt: int) -> float:
        if attempt < 1:
            raise ValueError("Attempt count starts at one")
        exponential = min(self.cap_seconds, self.base_seconds * 2 ** (attempt - 1))
        jitter_cap = min(1.0, exponential * 0.1)
        jitter = max(0.0, min(jitter_cap, self.jitter(jitter_cap)))
        return min(self.cap_seconds, exponential + jitter)

    def next_attempt_at(self, attempt: int, *, now: datetime | None = None) -> datetime:
        instant = now or datetime.now(UTC)
        return instant + timedelta(seconds=self.delay_seconds(attempt))
