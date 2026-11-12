"""Atomic fixed-window PostgreSQL admission counters with hashed identities."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from app.models import RateLimitWindow


class RateLimitExceeded(Exception):
    def __init__(self, retry_after: int):
        super().__init__("Webhook rate limit exceeded")
        self.retry_after = max(1, retry_after)


class RateLimitService:
    def __init__(self, *, sender_limit=10, group_limit=100, window_seconds=60):
        if min(sender_limit, group_limit, window_seconds) < 1:
            raise ValueError("Rate limits and window must be positive")
        self.sender_limit = sender_limit
        self.group_limit = group_limit
        self.window_seconds = window_seconds

    def reserve(self, session, *, channel: str, group_id: str, sender_id: str, now=None) -> int:
        instant = now or session.scalar(select(func.now()))
        bucket = int(instant.timestamp()) // self.window_seconds * self.window_seconds
        window_start = datetime.fromtimestamp(bucket, UTC)
        retry_after = max(
            1,
            int((window_start + timedelta(seconds=self.window_seconds) - instant).total_seconds()),
        )
        sender_key = self._hash(f"sender:{channel}:{group_id}:{sender_id}")
        group_key = self._hash(f"group:{channel}:{group_id}")
        self._reserve_one(
            session, "sender", sender_key, window_start, self.sender_limit, retry_after
        )
        self._reserve_one(session, "group", group_key, window_start, self.group_limit, retry_after)
        return retry_after

    @staticmethod
    def _hash(value: str) -> str:
        return sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _reserve_one(session, scope_type, scope_key, window_start, limit, retry_after):
        statement = (
            insert(RateLimitWindow)
            .values(
                scope_type=scope_type,
                scope_key=scope_key,
                window_start=window_start,
                request_count=1,
            )
            .on_conflict_do_update(
                index_elements=[
                    RateLimitWindow.scope_type,
                    RateLimitWindow.scope_key,
                    RateLimitWindow.window_start,
                ],
                set_={"request_count": RateLimitWindow.request_count + 1},
                where=RateLimitWindow.request_count < limit,
            )
            .returning(RateLimitWindow.id)
        )
        if session.scalar(statement) is None:
            raise RateLimitExceeded(retry_after)

    @staticmethod
    def cleanup_expired(session, *, before: datetime) -> int:
        result = session.execute(
            delete(RateLimitWindow).where(RateLimitWindow.window_start < before)
        )
        return result.rowcount or 0
