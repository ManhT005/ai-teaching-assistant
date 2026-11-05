import json
from pathlib import Path

import pytest

from app.ingestion import SourceMetadata, deduplicate_sources, eligible_demo_chunks, parse_document, parse_faq_xlsx
from app.rag import evaluate_demo_evidence
from scripts.check_sample_materials import load_sample_chunks

SAMPLES = Path(__file__).resolve().parents[1] / "data/samples"


def test_all_three_formats_parse_with_locators_and_deduplicate():
    chunks = load_sample_chunks()
    assert {Path(chunk.source_name).suffix for chunk in chunks} == {".docx", ".pdf", ".xlsx"}
    assert all(chunk.locator and chunk.version and chunk.section_id for chunk in chunks)
    assert any("page 1" in chunk.locator for chunk in chunks if chunk.source_name.endswith(".pdf"))
    assert any("knowledge!row" in chunk.locator for chunk in chunks if chunk.source_name.endswith(".xlsx"))
    unique = deduplicate_sources(chunks)
    assert len(unique) < len(chunks)
    assert sum(chunk.section_id == "AUTO-03" for chunk in unique) == 1
    assert sum(chunk.section_id == "AUTO-05" for chunk in unique) == 1


def test_xlsx_matches_auditable_json_and_keeps_draft_pending():
    chunks = parse_faq_xlsx(SAMPLES / "derived/knowledge_demo.xlsx")
    records = json.loads((SAMPLES / "knowledge_records.json").read_text(encoding="utf-8"))["records"]
    assert len(chunks) == len(records) == 8
    for chunk, row in zip(chunks, records):
        assert chunk.record_id == row["record_id"]
        assert chunk.content == f"Q: {row['question']}\nA: {row['answer']}"
        assert chunk.class_scope == row["class_scope"]
        assert chunk.review_status == row["review_status"]
        assert chunk.data_origin == row["data_origin"]
    draft = next(chunk for chunk in chunks if chunk.record_id == "DEMO-AUTO-003")
    assert draft.review_status == "pending" and draft.data_origin == "synthetic_demo"


def test_shared_faq_does_not_expose_other_class_deadline():
    chunks = load_sample_chunks()
    gs = eligible_demo_chunks(chunks, "GS-KPI-01")
    auto = eligible_demo_chunks(chunks, "AUTO-N8N-01")
    assert {chunk.class_scope for chunk in gs} == {"GS-KPI-01", "COMMON"}
    assert {chunk.class_scope for chunk in auto} == {"AUTO-N8N-01", "COMMON"}
    assert any(chunk.section_id == "GS-CLASS-01" for chunk in gs)
    assert all(chunk.section_id != "AUTO-CLASS-01" for chunk in gs)
    assert any(chunk.section_id == "AUTO-CLASS-01" for chunk in auto)
    assert all(chunk.section_id != "GS-CLASS-01" for chunk in auto)
    assert all(chunk.review_status != "pending" for chunk in gs + auto)


def test_rag_only_source_does_not_cover_comparison_even_when_relevant():
    decision = evaluate_demo_evidence(load_sample_chunks(), authorized_class="AUTO-N8N-01", required_topics={"rag_vs_finetuning"}, relevance_verified=True)
    assert decision.action == "handoff"
    assert decision.reason == "INCOMPLETE_EVIDENCE"
    assert not decision.sources


def test_hypothetical_demo_approval_still_needs_relevance_and_correct_class():
    # In-memory state transition test only: no fixture approval is written.
    chunks = parse_faq_xlsx(SAMPLES / "derived/knowledge_demo.xlsx")
    draft = next(chunk for chunk in chunks if chunk.record_id == "DEMO-AUTO-003")
    approved = draft.model_copy(update={"review_status": "approved_demo"})
    args = dict(required_topics={"rag_vs_finetuning"})
    assert evaluate_demo_evidence([approved], authorized_class="AUTO-N8N-01", **args).action == "handoff"
    assert evaluate_demo_evidence([approved], authorized_class="GS-KPI-01", relevance_verified=True, **args).action == "handoff"
    decision = evaluate_demo_evidence([approved], authorized_class="AUTO-N8N-01", relevance_verified=True, **args)
    assert decision.action == "answer" and decision.sources == (approved,)
    assert draft.review_status == "pending"


def test_missing_section_scope_fails_closed():
    source = json.loads((SAMPLES / "manifest.json").read_text(encoding="utf-8"))["documents"][0]
    source["metadata"]["sections"].pop("AUTO-05")
    with pytest.raises(ValueError, match="Missing operator scope"):
        parse_document(SAMPLES / source["path"], SourceMetadata.model_validate(source["metadata"]))


def test_unknown_authorized_class_is_rejected():
    with pytest.raises(ValueError, match="Unknown authorized class"):
        eligible_demo_chunks(load_sample_chunks(), "UNBOUND")
