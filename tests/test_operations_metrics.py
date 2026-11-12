from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db import get_db
from app.main import app
from app.models import Classroom, Event, MockDeliveredMessage, ModelUsage, OutboxMessage, Ticket


@pytest.fixture
def operator_client(rag_engine, monkeypatch):
    sessions = sessionmaker(rag_engine, expire_on_commit=False)
    with sessions.begin() as session:
        classroom = Classroom(code="OPERATOR-DEMO", name="Operator demo")
        session.add(classroom)
        session.flush()
        event = Event(
            channel="mock",
            external_event_id=uuid4().hex,
            group_id="operator-group",
            class_id=classroom.id,
            sender_id="private-sender-id",
            body="/hoi private question text",
            status="HANDOFF",
            attempts=2,
        )
        session.add(event)
        session.flush()
        event.finished_at = event.received_at
        session.add(
            Ticket(
                event_id=event.id,
                class_id=classroom.id,
                reason="INCOMPLETE_EVIDENCE",
                reason_code="INCOMPLETE_EVIDENCE",
                status="OPEN",
            )
        )
        session.add(
            ModelUsage(
                event_id=event.id,
                provider="fake",
                model="test",
                prompt_tokens=4,
                completion_tokens=2,
                estimated_cost_usd=None,
            )
        )
        outbox = OutboxMessage(
            event_id=event.id,
            message_key="test:operator",
            channel="mock",
            group_id=event.group_id,
            class_id=classroom.id,
            kind="HANDOFF_ACK",
            payload={"body": "Yêu cầu đã được ghi nhận.", "citations": []},
            status="SENT",
            attempts=1,
        )
        session.add(outbox)
        session.flush()
        session.add(
            MockDeliveredMessage(
                outbox_id=outbox.id,
                message_key=outbox.message_key,
                channel="mock",
                group_id=event.group_id,
                class_id=classroom.id,
                body=outbox.payload["body"],
                citations=[],
            )
        )
        event_id = event.id

    monkeypatch.setenv("OPERATOR_API_TOKEN", "operator-test-token")
    get_settings.cache_clear()

    def db_dependency():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = db_dependency
    try:
        with TestClient(app) as client:
            yield client, event_id
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_metrics_are_aggregate_and_keep_unknown_cost_null(operator_client):
    client, _ = operator_client
    response = client.get(
        "/internal/demo/metrics", headers={"X-Operator-Token": "operator-test-token"}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["events"]["HANDOFF"] == 1
    assert payload["by_class"]["OPERATOR-DEMO"]["HANDOFF"] == 1
    assert payload["retry_attempts"] == 1
    assert payload["outbox"]["SENT"] == 1
    assert payload["usage"]["estimated_cost_usd"] is None
    assert payload["usage"]["events_with_unknown_cost"] == 1
    assert "private-sender-id" not in response.text
    assert "private question text" not in response.text


def test_event_inspection_omits_question_and_sender(operator_client):
    client, event_id = operator_client
    response = client.get(
        f"/internal/demo/events/{event_id}",
        headers={"X-Operator-Token": "operator-test-token"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "HANDOFF"
    assert payload["decision"] == "HANDOFF"
    assert payload["delivery_status"] == "SENT"
    assert "sender_id" not in payload and "body" not in payload


def test_operator_deliveries_are_limited_and_sanitized(operator_client):
    client, _ = operator_client
    response = client.get(
        "/internal/demo/deliveries?limit=1",
        headers={"X-Operator-Token": "operator-test-token"},
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["kind"] == "HANDOFF_ACK"
    assert response.json()["items"][0]["body"] == "Yêu cầu đã được ghi nhận."
    assert (
        client.get(
            "/internal/demo/deliveries?limit=101",
            headers={"X-Operator-Token": "operator-test-token"},
        ).status_code
        == 422
    )
