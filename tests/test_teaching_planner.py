from types import SimpleNamespace

import pytest
from test_generation_contracts import source

from app.generation.contracts import AnswerDraft, Claim, ReasonCode
from app.generation.evidence import EvidenceDecision
from app.generation.pipeline import AnswerPipeline
from app.llm.provider import FakeLLM
from app.teaching.contracts import PresentationIntent
from app.teaching.coverage import ClaimCoverage
from app.teaching.planner import plan_teaching


@pytest.mark.parametrize("has_example", [False, True])
def test_pipeline_enforces_example_plan_before_returning_answer(has_example):
    text = (
        "Example: SUMIFS adds values for the matching region."
        if has_example
        else "SUMIFS sums values matching multiple criteria."
    )
    evidence = EvidenceDecision(
        ReasonCode.GROUNDED_ANSWER, {"c-1": (text,)}, (source(content=text),)
    )
    pipeline = AnswerPipeline(
        SimpleNamespace(search=lambda *_args, **_kwargs: None),
        SimpleNamespace(assess=lambda *_: evidence),
        FakeLLM(),
    )
    decision = pipeline.run("Example of SUMIFS", None)
    assert (decision.draft is not None) == has_example
    assert decision.reason == (
        ReasonCode.GROUNDED_ANSWER if has_example else ReasonCode.INCOMPLETE_EVIDENCE
    )
    assert decision.teaching_plan.intent == PresentationIntent.EXAMPLE


def draft(*texts):
    return AnswerDraft(
        answer_text="\n".join(texts),
        claims=[Claim(text=text, citation_ids=["c-1"]) for text in texts],
        needs_handoff=False,
    )


@pytest.mark.parametrize(
    "question,intent",
    [
        ("Giải thích SUMIFS", PresentationIntent.EXPLAIN),
        ("Cách sử dụng SUMIFS", PresentationIntent.HOWTO),
        ("n8n lỗi 401", PresentationIntent.TROUBLESHOOT),
        ("Cho em ví dụ SUMIFS", PresentationIntent.EXAMPLE),
    ],
)
def test_planner_uses_only_verbatim_supported_paragraphs(question, intent):
    text = "Ví dụ SUMIFS: một đoạn ví dụ nguyên văn từ tài liệu."
    evidence = EvidenceDecision(ReasonCode.GROUNDED_ANSWER, {"c-1": (text,)})
    plan = plan_teaching(question, draft(text), evidence)
    assert plan.intent == intent
    assert plan.coverage == ClaimCoverage.SUPPORTED
    assert plan.reason == ReasonCode.GROUNDED_ANSWER
    assert plan.claim_order == (0,)


def test_example_requires_actual_example_and_prioritizes_it():
    explanation = "SUMIFS sums values that satisfy multiple conditions."
    example = "Example: SUMIFS is applied to the following sample data."
    evidence = EvidenceDecision(ReasonCode.GROUNDED_ANSWER, {"c-1": (explanation, example)})
    assert (
        plan_teaching("Example of SUMIFS", draft(explanation), evidence).reason
        == ReasonCode.INCOMPLETE_EVIDENCE
    )
    plan = plan_teaching("Example of SUMIFS", draft(explanation, example), evidence)
    assert plan.claim_order == (1, 0)


def test_partial_claim_support_never_authorizes_an_answer():
    text = "The original supported source paragraph."
    evidence = EvidenceDecision(ReasonCode.GROUNDED_ANSWER, {"c-1": (text,)})
    plan = plan_teaching(
        "Explain SUMIFS", draft(text, "A model-invented additional instruction."), evidence
    )
    assert plan.coverage == ClaimCoverage.PARTIALLY_SUPPORTED
    assert plan.reason == ReasonCode.UNSUPPORTED_CLAIM


def test_wrong_citation_is_not_supported_by_another_chunk():
    text = "The original supported source paragraph."
    evidence = EvidenceDecision(ReasonCode.GROUNDED_ANSWER, {"c-2": (text,)})
    plan = plan_teaching("Explain SUMIFS", draft(text), evidence)
    assert plan.coverage == ClaimCoverage.UNSUPPORTED
    assert plan.reason == ReasonCode.UNSUPPORTED_CLAIM


@pytest.mark.parametrize(
    "question", ["So sánh RAG vs Fine-tuning", "Tại sao SUMIFS như vậy?", "Chính sách học phí?"]
)
def test_planner_preserves_conservative_comparison_causal_and_policy_gates(question):
    text = "A source paragraph containing related terms is not a full explanation."
    evidence = EvidenceDecision(ReasonCode.GROUNDED_ANSWER, {"c-1": (text,)})
    assert plan_teaching(question, draft(text), evidence).reason != ReasonCode.GROUNDED_ANSWER
