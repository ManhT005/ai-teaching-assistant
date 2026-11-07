from conftest import migrate
from sqlalchemy import inspect, text


def test_populated_generation_upgrade_downgrade_preserves_legacy_data(rag_database_factory):
    with rag_database_factory("0005_hybrid_retrieval") as engine:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO classes (code, name) VALUES ('AUTO-N8N-01', 'AUTO')")
            )
            connection.execute(
                text("""
                INSERT INTO events (channel, external_event_id, group_id, class_id, sender_id, body)
                SELECT 'mock', 'legacy', 'group', id, 'student', '/hoi 401' FROM classes
            """)
            )
            connection.execute(
                text("""
                INSERT INTO tickets (event_id, class_id, reason)
                SELECT id, class_id, 'legacy reason' FROM events
            """)
            )

        migrate(engine)
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT reason FROM tickets")) == "legacy reason"
            assert connection.scalar(text("SELECT reason_code FROM tickets")) is None
            connection.execute(
                text("""
                INSERT INTO model_usage (event_id, provider, model, prompt_tokens, completion_tokens)
                SELECT id, 'fake', 'test', NULL, NULL FROM events
            """)
            )
        assert "validation_summary" in {
            column["name"] for column in inspect(engine).get_columns("answers")
        }

        migrate(engine, "0005_hybrid_retrieval", downgrade=True)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT body FROM events")) == "/hoi 401"
            assert connection.scalar(text("SELECT reason FROM tickets")) == "legacy reason"
            assert connection.scalar(text("SELECT prompt_tokens FROM model_usage")) == 0
        migrate(engine)
        migrate(engine)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM tickets")) == 1
