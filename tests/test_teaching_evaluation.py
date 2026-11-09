"""Executable synthetic contracts; passing does not establish human teaching quality."""

import json
from pathlib import Path

import pytest

from app.context.contracts import ContextTurn
from app.context.rewriter import resolve_query
from app.generation.contracts import AnswerDraft, Claim, ReasonCode
from app.generation.evidence import EvidenceDecision
from app.teaching.planner import plan_teaching

DATASET = json.loads(
    Path("data/samples/contextual_teaching_eval_cases.json").read_text(encoding="utf-8")
)


def test_dataset_governance_does_not_claim_human_review():
    cases = DATASET["cases"]
    assert len(cases) == len({case["case_id"] for case in cases}) == 40
    assert sum(case["split"] == "dev" for case in cases) == 32
    assert sum(case["split"] == "holdout_candidate" for case in cases) == 8
    assert DATASET["holdout_sealed"] is False
    assert all(case["review_status"] == "pending" and case["reviewer"] is None for case in cases)


@pytest.mark.parametrize("case", DATASET["cases"], ids=lambda case: case["case_id"])
def test_synthetic_contextual_contract(case):
    if case["kind"] == "resolution":
        turns = tuple(ContextTurn(turn["event_id"], turn["question"]) for turn in case["history"])
        result = resolve_query(100, case["query"], turns)
        assert result.status.value == case["expected_resolution"]
        if result.status in {"STANDALONE", "RESOLVED"}:
            assert case["query"] in result.standalone_question
        if result.status == "STANDALONE":
            assert result.antecedent_event_ids == ()
    else:
        draft = AnswerDraft(
            answer_text="\n".join(case["claims"]),
            claims=[
                Claim(text=text, citation_ids=["synthetic-quote-1"]) for text in case["claims"]
            ],
            needs_handoff=False,
        )
        evidence = EvidenceDecision(ReasonCode.GROUNDED_ANSWER, case["evidence"])
        plan = plan_teaching(case["query"], draft, evidence)
        assert plan.intent.value == case["expected_intent"]
        assert plan.coverage.value == case["expected_coverage"]
        assert [label.value for label in plan.claim_labels] == case["expected_claim_labels"]
        assert plan.reason.value == case["expected_reason"]
