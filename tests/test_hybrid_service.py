from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from sqlalchemy import event, select, update
from test_ingestion_db import FakeEmbedding, section

from app.knowledge.store import KnowledgeStore
from app.models import ChannelBinding, Document, DocumentChunk
from app.retrieval.service import (
    HybridRetrievalService,
    ScopeAuthorizationError,
    normalize_question,
)


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
            .values(review_status="pending", data_origin="synthetic_demo")
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


@pytest.mark.parametrize("code", ["GS-KPI-01", "AUTO-N8N-01"])
def test_shared_common_sources_are_available_to_either_binding(hybrid, code):
    service, _, contexts = hybrid
    response = service.search("token", contexts[code])
    assert any(hit.class_scope == "COMMON" for hit in response.hits)
    assert any(hit.class_scope == "COMMON" for hit in response.diagnostics.lexical)


def test_sql_looking_question_cannot_change_database_or_widen_scope(hybrid):
    service, _, contexts = hybrid
    result = service.search(
        "401'); DROP TABLE documents; -- !SUMIFS & token:*", contexts["AUTO-N8N-01"]
    )

    assert result.diagnostics.lexical_terms == ("401", "sumifs", "token")
    assert all(hit.class_scope in {"AUTO-N8N-01", "COMMON"} for hit in result.hits)
    with service.sessions() as session:
        assert session.scalar(select(Document.id).limit(1)) is not None


def test_one_snapshot_prevents_metadata_drift_during_revoke(hybrid, monkeypatch):
    import app.retrieval.service as retrieval

    service, store, contexts = hybrid
    context = contexts["AUTO-N8N-01"]
    lexical = retrieval.lexical_candidates
    revoked = False

    def revoke_between_legs(*args, **kwargs):
        nonlocal revoked
        if not revoked:
            store.revoke("AUTO", "withdrawal between dense and lexical queries")
            revoked = True
        return lexical(*args, **kwargs)

    monkeypatch.setattr(retrieval, "lexical_candidates", revoke_between_legs)
    in_flight = service.search("401 Unauthorized", context)
    assert any(hit.doc_id == "AUTO" for hit in in_flight.diagnostics.dense)
    assert any(hit.doc_id == "AUTO" for hit in in_flight.diagnostics.lexical)

    after_revoke = service.search("401 Unauthorized", context)
    assert not any(
        hit.doc_id == "AUTO"
        for hit in (*after_revoke.diagnostics.dense, *after_revoke.diagnostics.lexical)
    )
