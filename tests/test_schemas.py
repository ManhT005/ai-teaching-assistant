import pytest
from pydantic import ValidationError
from app.schemas import MockMessageEvent

VALID = dict(event_id="e1", group_id="g1", sender_id="s1", text="/hoi test", received_at="2026-11-09T19:30:00+07:00")


def test_mock_channel_is_locked():
    with pytest.raises(ValidationError):
        MockMessageEvent(**VALID, channel="zalo")


def test_mock_event_has_required_identity():
    with pytest.raises(ValidationError):
        MockMessageEvent(**{**VALID, "event_id": ""})


def test_client_cannot_submit_class_id():
    with pytest.raises(ValidationError, match="class_id"):
        MockMessageEvent(**VALID, class_id="GS-KPI-01")


@pytest.mark.parametrize("field,value", [("received_at", "2026-11-09T19:30:00"), ("sender_id", "   "), ("group_id", "")])
def test_ambiguous_timestamp_and_blank_identifiers_are_rejected(field, value):
    with pytest.raises(ValidationError):
        MockMessageEvent(**{**VALID, field: value})

