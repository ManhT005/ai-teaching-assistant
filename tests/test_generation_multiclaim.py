"""Synthetic temporary fixtures; these are not reviewed GOS Academy course sources."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier, Lock

import pytest
from sqlalchemy import func, select
from test_generation_contracts import FIRST, SECOND, THIRD
from test_generation_events import add_event
from test_hybrid_service import QueryEmbedding
from test_ingestion_db import section

from app.events.processor import EventProcessor
from app.generation.contracts import AnswerSelection
from app.generation.evidence import EvidenceGate
from app.generation.evidence_catalog import build_catalog
from app.generation.pipeline import AnswerPipeline
from app.knowledge.ingestion import ReviewDecision
from app.knowledge.store import KnowledgeStore
from app.llm.provider import FakeLLM
from app.models import Answer, ChannelBinding, Document, DocumentChunk, Ticket
from app.retrieval.service import HybridRetrievalService


def synthetic_section(content, doc_id, section_id="AUTO-03", **changes):
    return section(
        content,
        doc_id=doc_id,
        section_id=section_id,
        class_scope="AUTO-N8N-01",
        source_name="synthetic-multiclaim-test.txt",
        locator="synthetic test paragraph",
        data_origin="synthetic_demo",
        review_status="approved_demo",
        review_decision=ReviewDecision(
            reviewer="automated-test-fixture-only",
            reviewed_at=datetime(2026, 11, 7, tzinfo=UTC),
            reason="Synthetic temporary test eligibility, not human or GOS course approval",
        ),
        **changes,
    )


@pytest.fixture
def multiclaim_system(authorized_contexts):
    sessions, contexts = authorized_contexts
    embeddings = QueryEmbedding()
    store = KnowledgeStore(sessions, embeddings)
    store.ingest(
        [
            synthetic_section(
                "AUTO-03 Synthetic test\n\n" + FIRST + "\n\n" + SECOND,
                "MC-A",
            )
        ]
    )
    store.ingest([synthetic_section(THIRD, "MC-B", "AUTO-04")])
    pipeline = AnswerPipeline(
        HybridRetrievalService(sessions, embeddings), EvidenceGate(sessions), FakeLLM()
    )
    scope = contexts["AUTO-N8N-01"]
    evidence = pipeline.gate.assess("401", scope, pipeline.retrieval.search("401", scope))
    assert evidence.sufficient
    assert len(build_catalog(evidence).quotes) == 3
    return sessions, contexts, store, pipeline, evidence


def chosen_ids(evidence, mode):
    catalog = build_catalog(evidence)
    same_chunk = next(hit.chunk_id for hit in evidence.hits if hit.doc_id == "MC-A")
    first_two = [quote.quote_id for quote in catalog.quotes if quote.chunk_id == same_chunk]
    other = next(quote.quote_id for quote in catalog.quotes if quote.chunk_id != same_chunk)
    return {
        "same-chunk": first_two,
        "different-chunks": [other, first_two[0]],
        "three": [other, first_two[1], first_two[0]],
    }[mode]


class SelectingProvider(FakeLLM):
    def __init__(self, ids):
        super().__init__()
        self.ids = ids
        self.calls = 0
        self.lock = Lock()

    def generate(self, question, evidence):
        with self.lock:
            self.calls += 1
        response = super().generate(question, evidence)
        return response.model_copy(
            update={
                "text": AnswerSelection(
                    needs_handoff=False,
                    quote_ids=self.ids,
                ).model_dump_json()
            }
        )


@pytest.mark.parametrize("mode", ["same-chunk", "different-chunks", "three"])
def test_persisted_claim_order_citation_dedup_and_replay(multiclaim_system, mode):
    sessions, contexts, _, pipeline, evidence = multiclaim_system
    ids = chosen_ids(evidence, mode)
    provider = SelectingProvider(ids)
    pipeline.provider = provider
    processor = EventProcessor(sessions, pipeline)
    event_id = add_event(sessions, contexts["AUTO-N8N-01"], "401")
    result = processor.process_event(event_id)
    assert result.status == "ANSWERED"
    quotes = [build_catalog(evidence).by_id()[quote_id] for quote_id in ids]
    expected_chunks = list(dict.fromkeys(quote.chunk_id for quote in quotes))
    assert [citation.chunk_id for citation in result.citations] == expected_chunks
    for index, quote in enumerate(quotes, start=1):
        number = expected_chunks.index(quote.chunk_id) + 1
        assert f"{index}. {quote.text} [{number}]" in result.reply_text
    assert processor.process_event(event_id) == result
    assert provider.calls == 1
    if mode == "three":
        Path(".pytest_cache/multiclaim-preview.json").write_text(
            json.dumps(
                {
                    "source": "synthetic temporary regression data; not GOS course content",
                    "status": result.status,
                    "reason_code": result.reason_code,
                    "reply_text": result.reply_text,
                    "citations": [citation.model_dump() for citation in result.citations],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    with sessions() as session:
        answer = session.scalar(select(Answer).where(Answer.event_id == event_id))
        assert answer.validation_summary["claims_validated"] == len(ids)
        assert answer.validation_summary["policy"] == "quote-selection-v1.1"
        assert len(answer.source_chunks) == len(expected_chunks)
        assert all("quote_id" not in citation for citation in answer.source_chunks)
        assert session.scalar(select(func.count()).select_from(Ticket)) == 0


@pytest.mark.parametrize("mutation", ["revoke", "pending", "content", "version", "binding"])
def test_multisource_changes_during_generation_prevent_publication(multiclaim_system, mutation):
    sessions, contexts, store, pipeline, evidence = multiclaim_system
    scope = contexts["AUTO-N8N-01"]
    selected_hit = next(hit for hit in evidence.hits if hit.doc_id == "MC-B")

    class ChangingProvider(SelectingProvider):
        def generate(self, question, allowed):
            assert sessions.kw["bind"].pool.checkedout() == 0
            response = super().generate(question, allowed)
            if mutation == "revoke":
                store.revoke("MC-B", "synthetic test withdrawal")
            else:
                with sessions.begin() as session:
                    if mutation == "pending":
                        session.get(DocumentChunk, selected_hit.row_id).review_status = "pending"
                    elif mutation == "content":
                        session.get(DocumentChunk, selected_hit.row_id).content = FIRST
                    elif mutation == "version":
                        session.get(Document, selected_hit.document_id).version += 1
                    else:
                        session.get(ChannelBinding, scope.binding_id).class_id = contexts[
                            "GS-KPI-01"
                        ].class_id
            return response

    pipeline.provider = ChangingProvider(chosen_ids(evidence, "three"))
    result = EventProcessor(sessions, pipeline).process_event(add_event(sessions, scope, "401"))
    assert result.status == "FAILED_FINAL" and result.reason_code == "SCOPE_REVOKED"
    assert result.reply_text is None and not result.citations
    assert result.model_usage.provider == "fake"
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Answer)) == 0


def test_identical_text_from_different_sources_keeps_both_citations(multiclaim_system):
    sessions, contexts, store, pipeline, _ = multiclaim_system
    store.revoke("MC-A", "replace synthetic fixture")
    store.revoke("MC-B", "replace synthetic fixture")
    store.ingest([synthetic_section(FIRST, "DUP-A")])
    store.ingest([synthetic_section(FIRST, "DUP-B")])
    pipeline.provider = FakeLLM(max_quotes=2)
    result = EventProcessor(sessions, pipeline).process_event(
        add_event(sessions, contexts["AUTO-N8N-01"], "401")
    )
    assert result.status == "ANSWERED"
    assert len(result.citations) == 2
    assert {citation.doc_id for citation in result.citations} == {"DUP-A", "DUP-B"}
    assert result.reply_text.count(FIRST) == 2


def test_partial_word_union_does_not_relax_frozen_evidence_policy(multiclaim_system):
    sessions, contexts, _, pipeline, _ = multiclaim_system
    # Authorization appears in one paragraph; credentials in a different source.
    result = EventProcessor(sessions, pipeline).process_event(
        add_event(sessions, contexts["AUTO-N8N-01"], "401 Authorization credentials")
    )
    assert result.status == "HANDOFF" and result.reason_code == "INCOMPLETE_EVIDENCE"
    assert result.model_usage is None
    foreign = EventProcessor(sessions, pipeline).process_event(
        add_event(sessions, contexts["GS-KPI-01"], "401")
    )
    assert foreign.status == "HANDOFF" and not foreign.citations


def test_concurrent_multiclaim_claim_has_one_call_and_one_answer(multiclaim_system):
    sessions, contexts, _, pipeline, evidence = multiclaim_system
    pipeline.provider = SelectingProvider(chosen_ids(evidence, "three"))
    event_id = add_event(sessions, contexts["AUTO-N8N-01"], "401")
    processor = EventProcessor(sessions, pipeline)
    barrier = Barrier(2)

    def process():
        barrier.wait(timeout=10)
        return processor.process_event(event_id)

    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(lambda _: process(), range(2)))
    assert any(result.status == "ANSWERED" for result in outcomes)
    assert pipeline.provider.calls == 1
    with sessions() as session:
        answer = session.scalar(select(Answer).where(Answer.event_id == event_id))
        assert answer.validation_summary["claims_validated"] == 3
        assert session.scalar(select(func.count()).select_from(Answer)) == 1


def test_invalid_id_after_valid_id_persists_only_ticket_and_usage(multiclaim_system):
    sessions, contexts, _, pipeline, evidence = multiclaim_system
    pipeline.provider = SelectingProvider([build_catalog(evidence).quotes[0].quote_id, "unknown"])
    result = EventProcessor(sessions, pipeline).process_event(
        add_event(sessions, contexts["AUTO-N8N-01"], "401")
    )
    assert result.status == "HANDOFF" and result.reason_code == "INVALID_CITATION"
    assert result.ticket_id and not result.citations and result.model_usage is not None
    assert pipeline.provider.calls == 1


@pytest.mark.parametrize(
    "payload,reason",
    [
        (
            {"needs_handoff": False, "quote_ids": ["q001", "q002", "q003", "q004"]},
            "UNSUPPORTED_CLAIM",
        ),
        ({"needs_handoff": False, "quote_ids": ["q001", "q001"]}, "INVALID_CITATION"),
        ({"needs_handoff": False, "quote_ids": []}, "UNSUPPORTED_CLAIM"),
        ({"needs_handoff": True, "quote_ids": []}, "INCOMPLETE_EVIDENCE"),
        ({"needs_handoff": True, "quote_ids": ["q001"]}, "UNSUPPORTED_CLAIM"),
        (
            {"needs_handoff": False, "quote_ids": ["q001"], "answer_text": "invented"},
            "UNSUPPORTED_CLAIM",
        ),
    ],
)
def test_invalid_or_handoff_selection_never_persists_an_answer(multiclaim_system, payload, reason):
    sessions, contexts, _, pipeline, _ = multiclaim_system

    class AdversarialProvider(FakeLLM):
        def generate(self, question, evidence):
            return (
                super()
                .generate(question, evidence)
                .model_copy(update={"text": json.dumps(payload)})
            )

    pipeline.provider = AdversarialProvider()
    result = EventProcessor(sessions, pipeline).process_event(
        add_event(sessions, contexts["AUTO-N8N-01"], "401")
    )
    assert result.status == "HANDOFF" and result.reason_code == reason
    assert result.ticket_id and result.model_usage is not None
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Answer)) == 0
