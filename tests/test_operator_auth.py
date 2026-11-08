from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import get_db
from app.main import app


def test_operator_auth_precedes_database_access(monkeypatch):
    monkeypatch.setenv("OPERATOR_API_TOKEN", "separate-operator-token")
    monkeypatch.setenv("MOCK_WEBHOOK_TOKEN", "different-webhook-token")
    get_settings.cache_clear()

    def forbidden_db():
        raise AssertionError("Unauthorized operator requests must not resolve the database")

    app.dependency_overrides[get_db] = forbidden_db
    try:
        client = TestClient(app)
        assert client.get("/internal/demo/metrics").status_code == 401
        assert (
            client.get(
                "/internal/demo/metrics",
                headers={"X-Webhook-Token": "different-webhook-token"},
            ).status_code
            == 401
        )
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
