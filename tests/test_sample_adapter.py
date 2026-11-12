import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.mock_channel import question_from_text
from app.schemas import MockMessageEvent
from scripts.convert_sample_events import convert_events

SAMPLES = Path(__file__).resolve().parents[1] / "data/samples"


def test_all_eight_samples_convert_without_changing_source_or_identity():
    original = json.loads((SAMPLES / "sample_events.original.json").read_text(encoding="utf-8-sig"))
    snapshot = deepcopy(original)
    converted = convert_events(original, add_trigger=True)["events"]
    assert len(converted) == 8
    assert original == snapshot
    for source, payload in zip(original["events"], converted):
        assert "class_id" not in payload
        assert payload["group_id"] == ("mock-google-sheets" if source["class_id"] == "GS-KPI-01" else "mock-ai-automation")
        for field in ("event_id", "sender_id", "received_at"):
            assert source[field] == payload[field]
        assert payload["text"] == "/hoi " + source["text"]
        MockMessageEvent.model_validate(payload)


def test_preserving_raw_messages_keeps_negative_trigger_cases():
    source = json.loads((SAMPLES / "sample_events.original.json").read_text(encoding="utf-8-sig"))
    output = convert_events(source)["events"]
    assert all(question_from_text(item["text"]) is None for item in output)


def test_unknown_sample_class_fails_instead_of_guessing():
    with pytest.raises(ValueError, match="Unknown sample class"):
        convert_events({"events": [{"class_id": "UNKNOWN"}]})


@pytest.mark.parametrize("text", ["RAG là gì?", "/hoiday", "/hoi", "/hoi   ", "/hoidan test"])
def test_trigger_requires_command_boundary_and_question(text):
    assert question_from_text(text) is None


def test_multiline_question_and_existing_prefix_are_preserved():
    text = "  /hoi RAG là gì?\nCho ví dụ."
    assert question_from_text(text) == "RAG là gì?\nCho ví dụ."
    source = {"events": [{"event_id": "E1", "class_id": "AUTO-N8N-01", "sender_id": "S1", "received_at": "2026-11-09T19:30:00+07:00", "text": text}]}
    assert convert_events(source, add_trigger=True)["events"][0]["text"] == text
