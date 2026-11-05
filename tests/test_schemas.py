import pytest
from pydantic import ValidationError
from app.schemas import MockMessageEvent


def test_mock_channel_is_locked():
    with pytest.raises(ValidationError):
        MockMessageEvent(event_id="e1", channel="zalo", group_id="g1", sender_id="s1", text="/hoi test")


def test_mock_event_has_required_identity():
    with pytest.raises(ValidationError):
        MockMessageEvent(event_id="", group_id="g1", sender_id="s1", text="/hoi test")
