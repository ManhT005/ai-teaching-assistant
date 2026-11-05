"""Convert local trusted sample JSON to mock payloads; no requests or credentials are emitted.

Usage: python -m scripts.convert_sample_events INPUT --output OUTPUT [--add-trigger]
The source file is never changed. Runtime class authorization remains in ChannelBinding.
"""
import argparse
import json
from pathlib import Path

from app.mock_channel import DEMO_GROUP_BY_CLASS, question_from_text
from app.schemas import MockMessageEvent


def convert_events(source: dict, *, add_trigger: bool = False) -> dict:
    events = []
    for item in source["events"]:
        class_code = item["class_id"]
        if class_code not in DEMO_GROUP_BY_CLASS:
            raise ValueError(f"Unknown sample class: {class_code}")
        text = item["text"]
        if add_trigger and question_from_text(text) is None:
            text = "/hoi " + text
        payload = MockMessageEvent(
            event_id=item["event_id"], group_id=DEMO_GROUP_BY_CLASS[class_code],
            sender_id=item["sender_id"], text=text, received_at=item["received_at"],
        ).model_dump(mode="json")
        # Preserve the exact input timestamp string, including its UTC offset.
        payload["received_at"] = item["received_at"]
        events.append(payload)
    return {"description": "Local mock fixtures; not real Zalo events", "events": events}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--add-trigger", action="store_true")
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error("Output must differ from the original input")
    result = convert_events(json.loads(args.input.read_text(encoding="utf-8-sig")), add_trigger=args.add_trigger)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Converted {len(result['events'])} events; no webhook was called.")


if __name__ == "__main__":
    main()
