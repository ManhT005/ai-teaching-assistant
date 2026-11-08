from datetime import UTC, datetime

import pytest

from app.events.retry import RetryPolicy


@pytest.mark.parametrize(
    ("attempt", "expected"),
    [(1, 2), (2, 4), (3, 8), (4, 16), (5, 32), (6, 60), (12, 60)],
)
def test_exponential_backoff_caps_and_bounds_jitter(attempt, expected):
    policy = RetryPolicy(base_seconds=2, cap_seconds=60, jitter=lambda _: 0)
    assert policy.delay_seconds(attempt) == expected


def test_retry_jitter_stays_within_bound():
    policy = RetryPolicy(base_seconds=2, cap_seconds=60, jitter=lambda ceiling: ceiling)
    assert policy.delay_seconds(1) == 2.2
    assert policy.delay_seconds(4) == 17


def test_retry_timestamp_uses_injected_clock_and_jitter():
    start = datetime(2026, 11, 8, tzinfo=UTC)
    policy = RetryPolicy(base_seconds=2, cap_seconds=60, jitter=lambda _: 0)
    assert policy.next_attempt_at(3, now=start) == datetime(2026, 11, 8, 0, 0, 8, tzinfo=UTC)


def test_retry_policy_rejects_invalid_attempt_or_delay():
    with pytest.raises(ValueError):
        RetryPolicy(base_seconds=0)
    with pytest.raises(ValueError):
        RetryPolicy().delay_seconds(0)
