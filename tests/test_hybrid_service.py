from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from sqlalchemy import event, select, update
from test_ingestion_db import FakeEmbedding, section

from app.knowledge_store import KnowledgeStore
from app.models import ChannelBinding, Document, DocumentChunk
from app.retrieval import HybridRetrievalService, ScopeAuthorizationError, normalize_question


class QueryEmbedding(FakeEmbedding):
    def encode_query(self, question):
        return self.encode_passages([question])[0]


@pytest.fixture
def hybrid(authorized_contexts):
    sessions, contexts = authorized_contexts
    embeddings = QueryEmbedding()
    store = KnowledgeStore(sessions, embeddings)
    store.ingest(
        [
            section("GS-02 Tổng\nSUMIFS COUNTIFS theo ngày", doc_id="GS"),
            section(
                "GS-04 Nhập\nIMPORTRANGE giá trị, không comment", doc_id="GS", section_id="GS-04"
            ),
        ]
    )
    store.ingest(
        [
            section(
                "AUTO-03 API\n401 Unauthorized HTTP Request token",
                doc_id="AUTO",
                section_id="AUTO-03",
                class_scope="AUTO-N8N-01",
            ),
            section(
                "AUTO-02 Webhook\nWebhook Test và Production",
                doc_id="AUTO",
                section_id="AUTO-02",
                class_scope="AUTO-N8N-01",
            ),
        ]
    )
    store.ingest(
        [
            section(
                "COMMON-02 Hỗ trợ\nMô tả lỗi, che token trước khi gửi",
                doc_id="SHARED",
                section_id="COMMON-02",
                class_scope="COMMON",
            )
        ]
    )

    return HybridRetrievalService(sessions, embeddings), store, contexts


def test_typed_citations_ranks_and_diagnostics_are_reproducible(hybrid):
    service, _, contexts = hybrid
    first = service.search("SUMIFS", contexts["GS-KPI-01"])
    repeated = service.search("SUMIFS", contexts["GS-KPI-01"])

    assert first.hits == repeated.hits
    assert first.diagnostics.dense == repeated.diagnostics.dense
    assert first.diagnostics.lexical == repeated.diagnostics.lexical
    assert len({hit.row_id for hit in first.hits}) == len(first.hits)
    assert any(hit.section_id == "GS-02" and hit.lexical_rank == 1 for hit in first.hits)
    assert all(
        hit.chunk_id
        and hit.doc_id
        and hit.document_id
        and hit.content
        and hit.locator
        and hit.source_name
        for hit in first.hits
    )
    assert all(hit.class_scope in {"GS-KPI-01", "COMMON"} for hit in first.hits)


@pytest.mark.parametrize("question", ["Em cần hỗ trợ", "SUMIFS"])
def test_no_terms_and_no_lexical_matches_keep_scoped_dense_fallback(hybrid, question):
    service, _, contexts = hybrid
    result = service.search(question, contexts["AUTO-N8N-01"])

    assert result.diagnostics.lexical == ()
    assert [hit.row_id for hit in result.hits] == [
        hit.row_id for hit in result.diagnostics.dense[:3]
    ]
    assert all(hit.lexical_rank is None for hit in result.hits)


def test_pending_revoked_and_old_versions_disappear_from_both_legs(hybrid):
    service, store, contexts = hybrid
    context = contexts["AUTO-N8N-01"]
    initial = service.search("401 Unauthorized", context)
    old_ids = {hit.row_id for hit in initial.diagnostics.dense if hit.doc_id == "AUTO"}
    assert any(hit.doc_id == "AUTO" for hit in initial.diagnostics.lexical)

    store.ingest(
        [
            section(
                "AUTO-03 API\n401 Unauthorized changed",
                doc_id="AUTO",
                section_id="AUTO-03",
                class_scope="AUTO-N8N-01",
            )
        ]
    )
    updated = service.search("401 Unauthorized", context)
    assert not old_ids & {
        hit.row_id for hit in (*updated.diagnostics.dense, *updated.diagnostics.lexical)
    }
    assert all(hit.version == 2 for hit in updated.diagnostics.dense if hit.doc_id == "AUTO")

    with store.sessions.begin() as session:
        doc_id = session.scalar(
            select(Document.id).where(Document.logical_key == "AUTO", Document.active.is_(True))
        )
        session.execute(
            update(DocumentChunk)
            .where(DocumentChunk.document_id == doc_id)
            .values(review_status="pending")
        )

    pending = service.search("401 Unauthorized", context)
    assert not any(
        hit.doc_id == "AUTO" for hit in (*pending.diagnostics.dense, *pending.diagnostics.lexical)
    )
    store.revoke("SHARED", "test immediate withdrawal")
    assert service.search("token", context).hits == ()


@pytest.mark.parametrize("question", ["", "  \n\t ", "a" * 1001, "text\x00", None])
def test_invalid_questions_fail_before_encoding(hybrid, monkeypatch, question):
    service, _, contexts = hybrid
    monkeypatch.setattr(
        service.embeddings, "encode_query", lambda _: pytest.fail("Invalid input reached model")
    )

    with pytest.raises(ValueError):
        service.search(question, contexts["GS-KPI-01"])


@pytest.mark.parametrize("top_k", [0, 6, True, "3"])
def test_invalid_top_k_is_rejected(hybrid, top_k):
    service, _, contexts = hybrid
    with pytest.raises(ValueError, match="top_k"):
        service.search("SUMIFS", contexts["GS-KPI-01"], top_k)


def test_untrusted_class_or_stale_binding_cannot_authorize_service(hybrid):
    service, _, contexts = hybrid
    context = contexts["GS-KPI-01"]

    with pytest.raises(ScopeAuthorizationError):
        service.search("401", {"class_id": "AUTO-N8N-01"})

    with pytest.raises(ScopeAuthorizationError):
        service.search("401", replace(context, class_code="AUTO-N8N-01"))

    with service.sessions.begin() as session:
        session.get(ChannelBinding, context.binding_id).active = False

    with pytest.raises(ScopeAuthorizationError):
        service.search("SUMIFS", context)


def test_concurrent_classes_have_no_context_bleed(hybrid):
    service, _, contexts = hybrid
    codes = ["GS-KPI-01", "AUTO-N8N-01"] * 6

    with ThreadPoolExecutor(max_workers=4) as workers:
        responses = list(
            workers.map(lambda code: service.search("SUMIFS 401 token", contexts[code]), codes)
        )

    for code, response in zip(codes, responses):
        assert response.authorized_class == code
        assert all(
            hit.class_scope in {code, "COMMON"}
            for hit in (*response.hits, *response.diagnostics.dense, *response.diagnostics.lexical)
        )


def test_search_is_read_only_and_does_not_hold_connection_during_encode(hybrid, monkeypatch):
    service, _, contexts = hybrid
    engine = service.sessions.kw["bind"]
    encode = service.embeddings.encode_query

    def check_connection(question):
        assert engine.pool.checkedout() == 0
        return encode(question)

    monkeypatch.setattr(service.embeddings, "encode_query", check_connection)
    statements = []

    def capture(connection, cursor, statement, parameters, context, many):
        statements.append(statement.lstrip().upper())

    event.listen(engine, "before_cursor_execute", capture)
    try:
        service.search("SUMIFS", contexts["GS-KPI-01"])
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert not any(statement.startswith(("INSERT", "UPDATE", "DELETE")) for statement in statements)
    assert "SET TRANSACTION READ ONLY" in statements


def test_normalization_preserves_formula_symbols_and_vietnamese():
    assert (
        normalize_question("  SUMIFS(A:A; B:B; 1)\n tiếng Việt  ")
        == "SUMIFS(A:A; B:B; 1) tiếng Việt"
    )
