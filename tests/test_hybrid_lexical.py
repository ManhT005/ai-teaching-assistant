import pytest
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker
from test_ingestion_db import FakeEmbedding, section

from app.knowledge.store import KnowledgeStore
from app.retrieval.lexical import extract_technical_terms, safe_or_query
from app.retrieval.service import lexical_candidates


def test_vietnamese_filler_produces_small_positive_or_query():
    terms = extract_technical_terms("Em chạy n8n bị lỗi 401 Unauthorized, em phải làm sao?")
    assert terms == ("n8n", "401", "unauthorized")
    assert safe_or_query(terms) == "n8n | 401 | unauthorized"
    assert extract_technical_terms("SUMIFS sumifs COUNTIFS IMPORTRANGE", 2) == (
        "sumifs",
        "countifs",
    )
    assert extract_technical_terms("Em cần hỗ trợ bài học") == ()


@pytest.mark.parametrize(
    "question",
    [
        "401'); DROP TABLE documents; -- SUMIFS",
        "!SUMIFS & (401 | 'IMPORTRANGE':*) <-> !n8n",
        "SELECT * FROM documents WHERE token = 'secret'",
    ],
)
def test_query_operators_never_survive_sanitization(question):
    query = safe_or_query(extract_technical_terms(question))
    assert all(
        operator not in query
        for operator in ("'", "!", "&", "(", ")", ":", "*", ";", "<", ">", "=")
    )


@pytest.mark.parametrize("terms", [("401 | !sumifs",), ("sumifs:*",), ("x' OR true",), ("é",)])
def test_direct_unsafe_lexemes_are_rejected(terms):
    with pytest.raises(ValueError, match="sanitized"):
        safe_or_query(terms)


def test_lexical_or_matches_technical_anchor_and_sql_is_bound(rag_engine):
    sessions = sessionmaker(rag_engine)
    store = KnowledgeStore(sessions, FakeEmbedding())
    store.ingest(
        [
            section(
                "AUTO-03 API\n401 Unauthorized",
                doc_id="AUTO",
                section_id="AUTO-03",
                class_scope="AUTO-N8N-01",
            )
        ]
    )
    store.ingest([section("GS-02 Tổng\n=SUMIFS(A:A;B:B;1)")])
    statements = []

    def capture(connection, cursor, statement, parameters, context, many):
        statements.append((statement, parameters))

    event.listen(rag_engine, "before_cursor_execute", capture)

    try:
        with sessions() as session:
            terms = extract_technical_terms("Em chạy n8n bị lỗi 401 Unauthorized")
            hits = lexical_candidates(session, "AUTO-N8N-01", terms)
            assert [hit.chunk.section_id for hit in hits] == ["AUTO-03"]
            assert lexical_candidates(session, "AUTO-N8N-01", ("sumifs",)) == []
            assert lexical_candidates(session, "GS-KPI-01", ()) == []
    finally:
        event.remove(rag_engine, "before_cursor_execute", capture)

    statement, parameters = statements[0]
    assert "n8n | 401 | unauthorized" not in statement
    assert "n8n | 401 | unauthorized" in parameters.values()
    assert statement.index("class_scope IN") < statement.index("LIMIT")


def test_lexical_ranking_is_stable_and_scoped_before_limit(rag_engine):
    sessions = sessionmaker(rag_engine)
    store = KnowledgeStore(sessions, FakeEmbedding())
    store.ingest([section("GS-02 Tổng\nSUMIFS", doc_id="GS")])
    store.ingest([section("AUTO-03 API\nSUMIFS " * 10, doc_id="AUTO", class_scope="AUTO-N8N-01")])

    with sessions() as session:
        first = lexical_candidates(session, "GS-KPI-01", ("sumifs",), limit=1)
        second = lexical_candidates(session, "GS-KPI-01", ("sumifs",), limit=1)

    assert first == second
    assert first[0].chunk.doc_id == "GS"
