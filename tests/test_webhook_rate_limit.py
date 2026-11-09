from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db import get_db
from app.events.rate_limit import RateLimitService
from app.main import app
from app.mock_channel import DEMO_CLASSES
from app.models import ChannelBinding, Classroom, Event, RateLimitWindow


@pytest.fixture
def rate_client(rag_engine, monkeypatch):
    sessions = sessionmaker(rag_engine, expire_on_commit=False)
    with sessions.begin() as session:
        for code, name, group in DEMO_CLASSES:
            classroom = Classroom(code=code, name=name)
            session.add(classroom)
            session.flush()
            session.add(
                ChannelBinding(channel="mock", group_id=group, class_id=classroom.id, active=True)
            )

    monkeypatch.setenv("MOCK_WEBHOOK_TOKEN", "local-rate-test")
    monkeypatch.setenv("WEBHOOK_SENDER_LIMIT", "1")
    monkeypatch.setenv("WEBHOOK_GROUP_LIMIT", "100")
    monkeypatch.setenv("WEBHOOK_RATE_WINDOW_SECONDS", "60")
    get_settings.cache_clear()

    def db_dependency():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = db_dependency
    try:
        with TestClient(app) as client:
            yield client, sessions
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def post(
    client,
    *,
    sender="sender-1",
    event_id=None,
    group="mock-ai-automation",
    text="/hoi rate test",
):
    return client.post(
        "/webhook/mock",
        headers={"X-Webhook-Token": "local-rate-test"},
        json={
            "event_id": event_id or uuid4().hex,
            "group_id": group,
            "sender_id": sender,
            "text": text,
            "received_at": "2026-11-08T12:00:00+07:00",
        },
    )


def test_sender_limit_returns_retry_after_and_rolls_back_event(rate_client):
    client, sessions = rate_client
    first = post(client)
    assert first.status_code == 202
    second = post(client, event_id="over-limit")
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) > 0

    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Event)) == 1
        assert session.scalar(select(func.count()).select_from(RateLimitWindow)) == 2


def test_duplicate_is_deduplicated_before_quota_even_when_full(rate_client):
    client, sessions = rate_client
    event_id = uuid4().hex
    assert post(client, event_id=event_id).status_code == 202
    duplicate = post(client, event_id=event_id)
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "duplicate"
    assert post(client, event_id="new-event-same-sender").status_code == 429
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Event)) == 1


def test_concurrent_duplicate_webhooks_charge_quota_once(rate_client):
    client, sessions = rate_client
    event_id = uuid4().hex
    with ThreadPoolExecutor(max_workers=8) as workers:
        responses = list(workers.map(lambda _: post(client, event_id=event_id), range(8)))
    assert sum(response.status_code == 202 for response in responses) == 1
    assert sum(response.status_code == 200 for response in responses) == 7
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Event)) == 1
        assert session.scalar(select(func.sum(RateLimitWindow.request_count))) == 2


def test_duplicate_with_altered_payload_does_not_replace_stored_event(rate_client):
    client, sessions = rate_client
    event_id = uuid4().hex
    assert post(client, event_id=event_id).status_code == 202
    replay = post(client, event_id=event_id, text="/hoi changed question")
    assert replay.status_code == 200
    with sessions() as session:
        event = session.scalar(select(Event).where(Event.external_event_id == event_id))
        assert event.body == "/hoi rate test"
        assert session.scalar(select(func.sum(RateLimitWindow.request_count))) == 2


def test_group_limit_is_atomic_under_concurrent_senders(rate_client, monkeypatch):
    client, sessions = rate_client
    monkeypatch.setenv("WEBHOOK_SENDER_LIMIT", "10")
    monkeypatch.setenv("WEBHOOK_GROUP_LIMIT", "3")
    get_settings.cache_clear()
    events = [f"concurrent-{index}" for index in range(8)]
    with ThreadPoolExecutor(max_workers=8) as workers:
        responses = list(
            workers.map(lambda event: post(client, sender=event, event_id=event), events)
        )

    assert sum(response.status_code == 202 for response in responses) == 3
    assert sum(response.status_code == 429 for response in responses) == 5
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Event)) == 3


def test_fixed_window_resets_and_expired_rows_can_be_cleaned(rag_engine):
    sessions = sessionmaker(rag_engine)
    limiter = RateLimitService(sender_limit=1, group_limit=2, window_seconds=60)
    first = datetime(2026, 11, 8, 0, 0, 0, tzinfo=UTC)
    next_window = first + timedelta(seconds=60)
    with sessions.begin() as session:
        limiter.reserve(
            session,
            channel="mock",
            group_id="group",
            sender_id="sender",
            now=first,
        )
    with sessions.begin() as session:
        limiter.reserve(
            session,
            channel="mock",
            group_id="group",
            sender_id="sender",
            now=next_window,
        )
        assert limiter.cleanup_expired(session, before=next_window) == 2
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(RateLimitWindow)) == 2


def test_sender_limiter_keys_do_not_store_raw_sender_id(rag_engine):
    sessions = sessionmaker(rag_engine)
    with sessions.begin() as session:
        RateLimitService().reserve(
            session, channel="mock", group_id="group", sender_id="private-sender"
        )
    with sessions() as session:
        keys = list(session.scalars(select(RateLimitWindow.scope_key)))
    assert all("private-sender" not in key for key in keys)
