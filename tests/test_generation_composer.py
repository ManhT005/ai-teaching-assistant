import pytest
from test_generation_contracts import FIRST, SECOND, THIRD, decision

from app.generation.contracts import AnswerSelection, ReasonCode
from app.generation.evidence_catalog import build_catalog
from app.generation.validator import validate_answer
from app.llm.provider import FakeLLM


@pytest.mark.parametrize("count", [1, 2, 3])
def test_fake_selection_composes_one_to_three_exact_claims(count):
    evidence = decision()
    catalog = build_catalog(evidence)
    response = FakeLLM(max_quotes=count).generate("401", catalog.provider_evidence())
    reason, draft = validate_answer(response.text, evidence)
    assert reason == ReasonCode.GROUNDED_ANSWER
    assert [claim.text for claim in draft.claims] == [FIRST, SECOND, THIRD][:count]
    assert draft.answer_text == "\n".join([FIRST, SECOND, THIRD][:count])
    assert [claim.citation_ids for claim in draft.claims] == [["c-1"], ["c-1"], ["c-2"]][:count]
    assert "Không gửi token" in draft.answer_text
    assert response.usage.prompt_tokens is None


def test_composition_preserves_selection_order():
    selection = AnswerSelection(needs_handoff=False, quote_ids=["q003", "q001"])
    reason, draft = validate_answer(selection.model_dump_json(), decision())
    assert reason == ReasonCode.GROUNDED_ANSWER
    assert [claim.text for claim in draft.claims] == [THIRD, FIRST]
    assert [claim.citation_ids for claim in draft.claims] == [["c-2"], ["c-1"]]


@pytest.mark.parametrize("count", [0, 4, True, "2"])
def test_fake_rejects_invalid_selection_count(count):
    with pytest.raises(ValueError):
        FakeLLM(max_quotes=count)
