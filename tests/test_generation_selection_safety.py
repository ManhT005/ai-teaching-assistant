import json
from dataclasses import replace

import pytest
from test_generation_contracts import FIRST, SECOND, decision, source
from test_generation_events import add_event

from app.events.processor import EventProcessor
from app.generation.contracts import AnswerSelection, ReasonCode
from app.generation.evidence import EvidenceDecision
from app.generation.evidence_catalog import CatalogError, build_catalog
from app.generation.validator import validate_answer, validate_composed_answer


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"needs_handoff": False, "quote_ids": []}, ReasonCode.UNSUPPORTED_CLAIM),
        ({"needs_handoff": True, "quote_ids": ["q001"]}, ReasonCode.UNSUPPORTED_CLAIM),
        ({"needs_handoff": True, "quote_ids": []}, ReasonCode.INCOMPLETE_EVIDENCE),
        ({"needs_handoff": False, "quote_ids": ["q001", "unknown"]}, ReasonCode.INVALID_CITATION),
        ({"needs_handoff": False, "quote_ids": ["q001", "q001"]}, ReasonCode.INVALID_CITATION),
        ({"needs_handoff": False, "quote_ids": ["q001"] * 4}, ReasonCode.UNSUPPORTED_CLAIM),
        (
            {"needs_handoff": False, "quote_ids": ["q001"], "answer_text": FIRST},
            ReasonCode.UNSUPPORTED_CLAIM,
        ),
        (
            {"needs_handoff": False, "quote_ids": ["q001"], "claims": []},
            ReasonCode.UNSUPPORTED_CLAIM,
        ),
        ({"needs_handoff": False, "quote_ids": "q001"}, ReasonCode.UNSUPPORTED_CLAIM),
        ({"needs_handoff": "false", "quote_ids": ["q001"]}, ReasonCode.UNSUPPORTED_CLAIM),
    ],
)
def test_selection_is_all_or_nothing(payload, expected):
    reason, draft = validate_answer(json.dumps(payload), decision())
    assert reason == expected
    assert draft is None


def test_answer_budget_counts_separators_and_never_truncates():
    def evidence_with_lengths(lengths):
        units = tuple(
            f"401 test {index} " + "x" * (length - 11) for index, length in enumerate(lengths)
        )
        assert [len(unit) for unit in units] == list(lengths)
        return EvidenceDecision(
            ReasonCode.GROUNDED_ANSWER,
            {"c-1": units},
            (source(content="\n\n".join(units)),),
        )

    raw = AnswerSelection(needs_handoff=False, quote_ids=["q001", "q002", "q003"]).model_dump_json()
    exact = evidence_with_lengths((2000, 2000, 1998))
    reason, draft = validate_answer(raw, exact)
    assert reason == ReasonCode.GROUNDED_ANSWER
    assert len(draft.answer_text) == 6000
    assert [claim.text for claim in draft.claims] == list(exact.evidence["c-1"])
    assert validate_answer(raw, evidence_with_lengths((2000, 2000, 2000)))[1] is None
    assert validate_answer(raw, evidence_with_lengths((2001, 20, 20)))[1] is None


def test_request_allowlist_is_an_immutable_copy():
    original = {"c-1": [FIRST]}
    evidence = EvidenceDecision(ReasonCode.GROUNDED_ANSWER, original, (source(),))
    original["c-1"].append(SECOND)
    assert evidence.evidence["c-1"] == (FIRST,)
    with pytest.raises(TypeError):
        evidence.evidence["c-1"] = (SECOND,)

    provider_input = build_catalog(evidence).provider_evidence()
    provider_input["c-1"] = (SECOND,)
    assert build_catalog(evidence).quotes[0].text == FIRST


def test_catalog_candidate_limit_rejects_inflation():
    units = tuple(f"401 synthetic test paragraph number {index}." for index in range(9))
    evidence = EvidenceDecision(
        ReasonCode.GROUNDED_ANSWER,
        {"c-1": units},
        (source(content="\n\n".join(units)),),
    )
    with pytest.raises(CatalogError):
        build_catalog(evidence)


def test_composed_draft_revalidation_rejects_text_and_citation_mutation():
    evidence = decision()
    raw = AnswerSelection(needs_handoff=False, quote_ids=["q001"]).model_dump_json()
    _, valid = validate_answer(raw, evidence)
    assert validate_composed_answer(valid, evidence) == ReasonCode.GROUNDED_ANSWER
    for changed, expected in [
        (valid.model_copy(update={"answer_text": "invented"}), ReasonCode.UNSUPPORTED_CLAIM),
        (valid.model_copy(update={"claims": []}), ReasonCode.UNSUPPORTED_CLAIM),
        (
            valid.model_copy(
                update={
                    "claims": [valid.claims[0].model_copy(update={"citation_ids": ["unknown"]})]
                }
            ),
            ReasonCode.INVALID_CITATION,
        ),
    ]:
        assert validate_composed_answer(changed, evidence) == expected


def test_event_rejects_composed_draft_modified_after_validation(generation_system, monkeypatch):
    sessions, contexts, _, pipeline = generation_system
    run = pipeline.run

    def tamper(question, scope):
        result = run(question, scope)
        return replace(result, draft=result.draft.model_copy(update={"answer_text": "invented"}))

    monkeypatch.setattr(pipeline, "run", tamper)
    result = EventProcessor(sessions, pipeline).process_event(
        add_event(sessions, contexts["AUTO-N8N-01"])
    )
    assert result.status == "HANDOFF" and result.reason_code == "UNSUPPORTED_CLAIM"
    assert result.ticket_id and not result.citations
    assert result.model_usage.provider == "fake"
