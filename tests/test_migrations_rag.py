import pytest
from conftest import migrate
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError


def test_fresh_schema_has_fixed_vector_and_lifecycle_constraints(rag_engine):
    with rag_engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0008_contextual_teaching"
        )
        vector_type = connection.scalar(
            text("""
            SELECT format_type(a.atttypid, a.atttypmod)
            FROM pg_attribute a
            WHERE a.attrelid = 'document_chunks'::regclass AND a.attname = 'embedding'
        """)
        )
        assert vector_type == "vector(384)"

    constraints = {
        check["name"] for check in inspect(rag_engine).get_check_constraints("documents")
    }
    assert {
        "ck_document_embedding",
        "ck_document_active_ready",
        "ck_document_provenance",
    } <= constraints
    assert "source_received_at" in {
        column["name"] for column in inspect(rag_engine).get_columns("events")
    }


def test_context_snapshot_migration_can_downgrade_and_upgrade(rag_database_factory):
    with rag_database_factory() as engine:
        inspector = inspect(engine)
        assert inspector.has_table("event_context_resolutions", schema=engine.rag_test_schema)

        migrate(engine, "0007_event_reliability", downgrade=True)

        inspector = inspect(engine)
        assert not inspector.has_table("event_context_resolutions", schema=engine.rag_test_schema)

        migrate(engine)

        inspector = inspect(engine)
        assert inspector.has_table("event_context_resolutions", schema=engine.rag_test_schema)


def test_day_one_upgrade_keeps_legacy_rows_but_requires_reindex(rag_database_factory):
    with rag_database_factory("0002_source_received_at") as engine:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO documents (logical_key, source_name, active) VALUES ('legacy', 'legacy.pdf', true)"
                )
            )
            connection.execute(
                text("""
                INSERT INTO document_chunks (document_id, chunk_index, class_scope, content)
                SELECT id, 0, 'COMMON', 'legacy content' FROM documents
            """)
            )

        migrate(engine)

        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT active, review_status, index_status FROM documents")
            ).one()
            assert tuple(row) == (False, "pending", "FAILED")
            assert connection.scalar(text("SELECT count(*) FROM document_chunks")) == 1


def test_incompatible_legacy_vector_aborts_entire_migration(rag_database_factory):
    with rag_database_factory("0002_source_received_at") as engine:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO documents (logical_key, source_name, active) VALUES ('legacy', 'x.pdf', true)"
                )
            )
            connection.execute(
                text("""
                INSERT INTO document_chunks (document_id, chunk_index, class_scope, content, embedding)
                SELECT id, 0, 'COMMON', 'text', '[1,2,3]'::vector FROM documents
            """)
            )

        with pytest.raises(DBAPIError):
            migrate(engine)

        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0002_source_received_at"
            )
            assert connection.scalar(text("SELECT active FROM documents")) is True
            assert (
                connection.scalar(text("SELECT vector_dims(embedding) FROM document_chunks")) == 3
            )
