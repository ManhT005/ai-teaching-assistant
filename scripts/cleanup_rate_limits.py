"""Prune expired hashed webhook quota buckets from PostgreSQL."""

from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.db import get_session_factory
from app.events.rate_limit import RateLimitService


def main(*, sessions=None):
    settings = get_settings()
    limiter = RateLimitService(
        sender_limit=settings.webhook_sender_limit,
        group_limit=settings.webhook_group_limit,
        window_seconds=settings.webhook_rate_window_seconds,
    )
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.webhook_rate_window_seconds * 2)
    sessions = sessions or get_session_factory()
    with sessions.begin() as session:
        deleted = limiter.cleanup_expired(session, before=cutoff)
    print(f"Expired rate-limit windows removed: {deleted}")
    return deleted


if __name__ == "__main__":
    main()
