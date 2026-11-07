import json
from pathlib import Path

from test_generation_events import add_event

from app.events.processor import EventProcessor


def test_developer_eval_cases_match_persisted_scoped_outcomes(generation_system):
    sessions, contexts, _, pipeline = generation_system
    dataset = json.loads(
        Path("data/samples/generation_eval_cases.json").read_text(encoding="utf-8")
    )
    assert dataset["review_status"] == "developer_reviewed"
    assert len({case["id"] for case in dataset["cases"]}) == len(dataset["cases"])
    processor = EventProcessor(sessions, pipeline)

    for case in dataset["cases"]:
        scope = contexts[case["class_code"]]
        result = processor.process_event(add_event(sessions, scope, case["question"]))
        assert result.status == case["expected_status"], case["id"]
        assert result.reason_code == case["reason"], case["id"]
        assert {citation.section_id for citation in result.citations} == set(
            case["citation_sections"]
        )
        assert all(
            citation.class_scope in {scope.class_code, "COMMON"} for citation in result.citations
        )
        if result.status == "HANDOFF":
            assert result.ticket_id is not None and result.model_usage is None
