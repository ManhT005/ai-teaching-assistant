import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker
from test_ingestion_db import FakeEmbedding, section

from app.config import Settings
from app.knowledge_store import KnowledgeStore
from app.models import Document, DocumentChunk
from scripts.smoke_vector_search import search


@pytest.fixture
def scoped_store(rag_engine):
    store = KnowledgeStore(sessionmaker(rag_engine), FakeEmbedding(), Settings())
    for scope, doc_id in (("GS-KPI-01", "GS"), ("AUTO-N8N-01", "AUTO"), ("COMMON", "SHARED")):
        store.ingest([section(doc_id=doc_id, class_scope=scope)])
    return store


def test_scope_review_and_inactive_filter_precedes_cosine_ranking(scoped_store):
    store = scoped_store
    vector = store.embeddings.encode_passages([section().content])[0]

    with store.sessions() as session:
        gs = search(session, "GS-KPI-01", vector, 5)
        auto = search(session, "AUTO-N8N-01", vector, 5)

    assert {hit["doc_id"] for hit in gs} == {"GS", "SHARED"}
    assert {hit["doc_id"] for hit in auto} == {"AUTO", "SHARED"}
    assert all(hit["locator"] and hit["distance"] < 1e-5 for hit in gs + auto)

    with store.sessions.begin() as session:
        gs_document = session.scalar(select(Document).where(Document.logical_key == "GS"))
        session.execute(
            update(DocumentChunk)
            .where(DocumentChunk.document_id == gs_document.id)
            .values(review_status="pending")
        )

    with store.sessions() as session:
        assert {hit["doc_id"] for hit in search(session, "GS-KPI-01", vector, 5)} == {"SHARED"}

    store.revoke("SHARED", "urgent policy withdrawal")

    with store.sessions() as session:
        assert search(session, "GS-KPI-01", vector, 5) == []
        assert {hit["doc_id"] for hit in search(session, "AUTO-N8N-01", vector, 5)} == {"AUTO"}


def test_inactive_old_version_never_returns_even_with_exact_vector(scoped_store):
    store = scoped_store
    old_vector = store.embeddings.encode_passages([section().content])[0]
    store.ingest([section("GS-02 Tổng\nupdated", doc_id="GS")])

    with store.sessions() as session:
        hits = search(session, "GS-KPI-01", old_vector, 5)

    assert [hit["version"] for hit in hits if hit["doc_id"] == "GS"] == [2]


@pytest.mark.parametrize("class_code, top_k", [("UNBOUND", 3), ("COMMON", 3), ("GS-KPI-01", 6)])
def test_unknown_class_or_unbounded_top_k_is_rejected(scoped_store, class_code, top_k):
    vector = scoped_store.embeddings.encode_passages([section().content])[0]

    with scoped_store.sessions() as session, pytest.raises(ValueError, match="authorized"):
        search(session, class_code, vector, top_k)
