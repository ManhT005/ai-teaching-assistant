from app.config import Settings


def test_webhook_and_operator_tokens_are_hidden_from_settings_repr():
    settings = Settings(
        mock_webhook_token="webhook-secret-value",
        operator_api_token="operator-secret-value",
    )
    rendered = repr(settings)
    assert "webhook-secret-value" not in rendered
    assert "operator-secret-value" not in rendered
    assert "**********" in rendered
