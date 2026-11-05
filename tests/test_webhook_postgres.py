"""Real PostgreSQL tests; required in CI, skipped locally without TEST_DATABASE_URL."""
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.main import app
from app.models import Classroom, ChannelBinding, Event
from app.mock_channel import DEMO_CLASSES


@pytest.fixture
def client_db(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration")
    engine = create_engine(url)
    with engine.connect() as connection:
        transaction = connection.begin()
        with Session(bind=connection, join_transaction_mode="create_savepoint") as db:
            for code, name, group in DEMO_CLASSES:
                classroom = db.scalar(select(Classroom).where(Classroom.code == code))
                if classroom is None:
                    classroom = Classroom(code=code, name=name, bot_enabled=True)
                    db.add(classroom)
                    db.flush()
                binding = db.scalar(select(ChannelBinding).where(ChannelBinding.channel == "mock", ChannelBinding.group_id == group))
                if binding is None:
                    db.add(ChannelBinding(channel="mock", group_id=group, class_id=classroom.id, active=True))
            db.commit()
            monkeypatch.setenv("MOCK_WEBHOOK_TOKEN", "local-test-only")
            get_settings.cache_clear()
            app.dependency_overrides[get_db] = lambda: db
            try:
                with TestClient(app) as client:
                    yield client, db
            finally:
                app.dependency_overrides.clear()
                get_settings.cache_clear()
        transaction.rollback()
    engine.dispose()


def payload(**changes):
    return {"event_id": str(uuid4()), "group_id": "mock-ai-automation", "sender_id": "S1", "text": "/hoi test", "received_at": "2026-11-09T19:30:00+07:00", **changes}


def post(client, event):
    return client.post("/webhook/mock", json=event, headers={"X-Webhook-Token": "local-test-only"})


def test_class_mapping_timestamp_and_duplicate_intake(client_db):
    client, db = client_db
    for group, code in [("mock-ai-automation", "AUTO-N8N-01"), ("mock-google-sheets", "GS-KPI-01")]:
        event = payload(group_id=group)
        assert post(client, event).status_code == 202
        replay = post(client, event)
        assert replay.status_code == 200 and replay.json()["status"] == "duplicate"
        stored = db.scalar(select(Event).where(Event.external_event_id == event["event_id"]))
        assert stored.class_id == db.scalar(select(Classroom.id).where(Classroom.code == code))
        assert stored.source_received_at.isoformat() == "2026-11-09T12:30:00+00:00"
        assert stored.received_at is not None
        assert db.scalar(select(func.count()).select_from(Event).where(Event.external_event_id == event["event_id"])) == 1


def test_group_and_client_class_cannot_bypass_scope(client_db):
    client, _ = client_db
    assert post(client, payload(group_id="unknown-group")).status_code == 403
    assert post(client, payload(group_id="unknown-group", text="ordinary chat")).status_code == 403
    assert post(client, payload(class_id="GS-KPI-01")).status_code == 422


@pytest.mark.parametrize("changes", [{"text": "ordinary chat"}, {"text": "/hoiday test"}, {"text": "/hoi"}, {"is_bot": True}])
def test_ignored_messages_are_not_queued(client_db, changes):
    client, db = client_db
    event = payload(**changes)
    response = post(client, event)
    assert response.json()["status"] == "ignored"
    assert db.scalar(select(Event).where(Event.external_event_id == event["event_id"])) is None


def test_disabled_class_and_inactive_binding(client_db):
    client, db = client_db
    classroom = db.scalar(select(Classroom).where(Classroom.code == "AUTO-N8N-01"))
    classroom.bot_enabled = False
    db.commit()
    assert post(client, payload()).json()["status"] == "ignored"
    binding = db.scalar(select(ChannelBinding).where(ChannelBinding.group_id == "mock-ai-automation"))
    binding.active = False
    db.commit()
    assert post(client, payload()).status_code == 403
