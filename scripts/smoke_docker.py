"""Exercise the running Docker API and real polling workers using simulated demo questions."""

import json
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select

from app.config import get_settings
from app.db import get_session_factory
from app.models import Event, MockDeliveredMessage, OutboxMessage


def main():
    settings = get_settings()
    if settings.llm_provider != "fake":
        raise SystemExit("Docker smoke requires LLM_PROVIDER=fake; it never calls a live provider")
    sessions = get_session_factory()
    operator = {"X-Operator-Token": settings.operator_api_token.get_secret_value()}
    webhook = {"X-Webhook-Token": settings.mock_webhook_token.get_secret_value()}

    def request(path, headers=None, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {**(headers or {}), "Content-Type": "application/json"}
        req = urllib.request.Request("http://127.0.0.1:8000" + path, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.load(response)

    assert request("/health")["status"] == "ok"
    assert request("/ready")["vector"] is True
    try:
        request("/internal/demo/metrics")
    except urllib.error.HTTPError as error:
        assert error.code == 401
    else:
        raise AssertionError("Operator API accepted an unauthenticated request")

    results = []
    for question, expected in (
        ("/hoi Lỗi HTTP 401 trong n8n cần kiểm tra gì?", "ANSWER"),
        ("/hoi Khóa học tiếp theo có giảm học phí không?", "HANDOFF"),
    ):
        external_id = "docker-smoke-" + uuid4().hex
        body = {
            "event_id": external_id,
            "channel": "mock",
            "group_id": "mock-ai-automation",
            "sender_id": external_id,
            "text": question,
            "received_at": datetime.now(UTC).isoformat(),
        }
        assert request("/webhook/mock", webhook, body)["status"] == "accepted"
        assert request("/webhook/mock", webhook, body)["status"] == "duplicate"
        deadline = time.monotonic() + 90
        status = None
        while time.monotonic() < deadline:
            with sessions() as session:
                event_id = session.scalar(
                    select(Event.id).where(Event.external_event_id == external_id)
                )
            status = request(f"/internal/demo/events/{event_id}", operator)
            if status["delivery_status"] == "SENT":
                break
            if status["status"] == "FAILED_FINAL":
                raise AssertionError(f"Worker failed: {status['reason_code']}")
            time.sleep(1)
        assert status["delivery_status"] == "SENT", status
        assert status["decision"] == expected, status
        assert status["class_code"] == "AUTO-N8N-01"
        if expected == "ANSWER":
            assert status["citation_count"] > 0
        else:
            assert status["ticket_status"] == "OPEN"
        with sessions() as session:
            count = session.scalar(
                select(func.count())
                .select_from(MockDeliveredMessage)
                .join(OutboxMessage, OutboxMessage.id == MockDeliveredMessage.outbox_id)
                .where(OutboxMessage.event_id == event_id)
            )
        assert count == 1, "Duplicate event produced duplicate mock deliveries"
        results.append({"event_id": event_id, "decision": expected, "delivery": "SENT"})
    print(json.dumps({"docker_smoke": "passed", "results": results}))


if __name__ == "__main__":
    main()
