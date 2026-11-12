import pytest
from pydantic import ValidationError

from app.generation.contracts import AnswerSelection, ReasonCode
from app.generation.evidence import EvidenceDecision
from app.generation.evidence_catalog import CatalogError, build_catalog
from app.retrieval.service import RetrievedChunk

FIRST = "401: kiểm tra thông tin xác thực. Không gửi token lên nhóm."
SECOND = "401: kiểm tra header Authorization trước khi thử lại."
THIRD = "401: kiểm tra thời hạn token trong phần credentials."


def source(chunk_id="c-1", content=FIRST, **changes):
    values = {
        "row_id": 1,
        "chunk_id": chunk_id,
        "document_id": 1,
        "doc_id": "SYNTHETIC-TEST",
        "version": 1,
        "external_version": "TEST-1",
        "section_id": "AUTO-03",
        "source_name": "synthetic-test.txt",
        "locator": "test paragraph",
        "class_scope": "AUTO-N8N-01",
        "content": content,
    }
    return RetrievedChunk(**{**values, **changes})


def decision():
    # Deliberately reversed allowlist order; hit/source order is authoritative.
    return EvidenceDecision(
        ReasonCode.GROUNDED_ANSWER,
        {"c-2": (THIRD,), "c-1": (SECOND, FIRST)},
        (source(content=FIRST + "\n\n" + SECOND), source("c-2", THIRD, row_id=2)),
    )


@pytest.mark.parametrize("count", [0, 1, 3])
def test_strict_selection_cardinality(count):
    selected = AnswerSelection(
        needs_handoff=count == 0, quote_ids=[f"q{i:03}" for i in range(count)]
    )
    assert len(selected.quote_ids) == count


@pytest.mark.parametrize(
    "changes",
    [
        {"quote_ids": ["q001"] * 4},
        {"quote_ids": "q001"},
        {"quote_ids": [1]},
        {"needs_handoff": "false"},
        {"needs_handoff": 0},
        {"answer_text": "invented"},
    ],
)
def test_selection_rejects_invalid_types_extra_prose_and_four_choices(changes):
    with pytest.raises(ValidationError):
        AnswerSelection(**{"needs_handoff": False, "quote_ids": ["q001"], **changes})


def test_catalog_is_stable_in_source_order_with_exact_identity():
    evidence = decision()
    catalog = build_catalog(evidence)
    assert catalog == build_catalog(evidence)
    assert [(quote.quote_id, quote.chunk_id, quote.text) for quote in catalog.quotes] == [
        ("q001", "c-1", FIRST),
        ("q002", "c-1", SECOND),
        ("q003", "c-2", THIRD),
    ]
    assert catalog.provider_evidence() == {"c-1": (FIRST, SECOND), "c-2": (THIRD,)}
    assert evidence.hits[0].version == 1 and evidence.hits[0].locator == "test paragraph"


def test_one_quote_and_identical_text_in_two_sources_keep_provenance():
    one = EvidenceDecision(ReasonCode.GROUNDED_ANSWER, {"c-1": (FIRST,)}, (source(),))
    assert len(build_catalog(one).quotes) == 1
    two = EvidenceDecision(
        ReasonCode.GROUNDED_ANSWER,
        {"c-1": (FIRST,), "c-2": (FIRST,)},
        (source(), source("c-2", row_id=2)),
    )
    assert [quote.chunk_id for quote in build_catalog(two).quotes] == ["c-1", "c-2"]


def test_catalog_rejects_conflicts_missing_hits_and_modified_text():
    for evidence in (
        EvidenceDecision(ReasonCode.GROUNDED_ANSWER, {"c-1": (FIRST,)}, ()),
        EvidenceDecision(ReasonCode.GROUNDED_ANSWER, {"c-1": (SECOND,)}, (source(),)),
        EvidenceDecision(
            ReasonCode.GROUNDED_ANSWER, {"c-1": (FIRST,)}, (source(), source(version=2))
        ),
        EvidenceDecision(ReasonCode.GROUNDED_ANSWER, {"c-1": (FIRST[:30],)}, (source(),)),
    ):
        with pytest.raises(CatalogError):
            build_catalog(evidence)
