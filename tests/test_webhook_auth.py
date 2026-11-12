from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import get_db
from app.main import app


def test_authentication_precedes_database_access(monkeypatch):
    monkeypatch.setenv("MOCK_WEBHOOK_TOKEN", "local-test-only")
    get_settings.cache_clear()

    def forbidden_db():
        raise AssertionError("Unauthorized requests must not resolve the database")

    app.dependency_overrides[get_db] = forbidden_db
    try:
        response = TestClient(app).post("/webhook/mock", json={
            "event_id": "E1", "group_id": "mock-ai-automation", "sender_id": "S1",
            "text": "/hoi test", "received_at": "2026-11-09T19:30:00+07:00",
        })
        assert response.status_code == 401
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
