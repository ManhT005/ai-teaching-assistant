import pytest
from conftest import migrate
from sqlalchemy import inspect, text


def test_generated_fts_and_gin_exist_on_fresh_database(rag_engine):
    inspector = inspect(rag_engine)
    column = next(
        column
        for column in inspector.get_columns("document_chunks")
        if column["name"] == "search_vector"
    )

    assert column["computed"]["persisted"] is True
    assert "simple" in column["computed"]["sqltext"]
    assert any(
        index["name"] == "ix_document_chunks_search_vector_gin"
        for index in inspector.get_indexes("document_chunks")
    )


def test_populated_upgrade_downgrade_and_repeated_upgrade_preserve_indexed_data(
    rag_database_factory,
):
    with rag_database_factory("0004_ingestion_provenance") as engine:
        with engine.begin() as connection:
            connection.execute(
                text("""
                INSERT INTO documents (logical_key, source_name, active, index_status,
                    review_status, indexed_at) VALUES
                    ('original', 'original.docx', true, 'READY', 'provided_sample', now())
            """)
            )
            connection.execute(
                text("""
                INSERT INTO document_chunks
                    (document_id, chunk_index, class_scope, content, embedding)
                SELECT id, 0, 'COMMON', 'SUMIFS COUNTIFS n8n 401 IMPORTRANGE',
                    array_prepend(1.0, array_fill(0.0, ARRAY[383]))::vector
                FROM documents
            """)
            )
            before = connection.execute(
                text("SELECT id, content, embedding::text FROM document_chunks")
            ).all()

        for _ in range(2):
            migrate(engine)

        with engine.begin() as connection:
            assert connection.scalar(
                text(
                    "SELECT search_vector @@ to_tsquery('simple', '401 | sumifs') FROM document_chunks"
                )
            )
            connection.execute(text("UPDATE document_chunks SET content = content || ' webhook'"))
            assert connection.scalar(
                text("SELECT search_vector @@ to_tsquery('simple', 'webhook') FROM document_chunks")
            )

        migrate(engine, "0004_ingestion_provenance", downgrade=True)

        with engine.connect() as connection:
            assert connection.scalar(text("SELECT active FROM documents")) is True
            after = connection.execute(
                text("SELECT id, content, embedding::text FROM document_chunks")
            ).all()
            assert after[0].id == before[0].id
            assert after[0].content == before[0].content + " webhook"
            assert after[0][2] == before[0][2]

        assert "search_vector" not in {
            column["name"] for column in inspect(engine).get_columns("document_chunks")
        }
        migrate(engine)

        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT search_vector @@ to_tsquery('simple', 'webhook') FROM document_chunks")
            )


@pytest.mark.parametrize(
    "term",
    ["401", "403", "429", "sumifs", "countifs", "importrange", "n8n", "webhook", "http", "request"],
)
def test_postgres_simple_tokenizer_recognizes_technical_anchors(rag_engine, term):
    with rag_engine.connect() as connection:
        matched = connection.scalar(
            text("SELECT to_tsvector('simple', :content) @@ to_tsquery('simple', :term)"),
            {
                "content": "=SUMIFS(A:A;B:B;1) COUNTIFS IMPORTRANGE n8n HTTP Request Webhook 401 403 429",
                "term": term,
            },
        )
        assert matched is True


def test_simple_dictionary_does_not_remove_vietnamese_accents(rag_engine):
    with rag_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT to_tsvector('simple', 'học phí') @@ to_tsquery('simple', 'hoc')")
            )
            is False
        )
