from conftest import migrate
from sqlalchemy import inspect, text


def test_populated_0008_upgrade_keeps_original_handoff(rag_database_factory):
    with rag_database_factory("0008_contextual_teaching") as engine:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO classes(id,code,name) VALUES (1,'AUTO-N8N-01','fixture')")
            )
            connection.execute(
                text(
                    "INSERT INTO events(id,channel,external_event_id,group_id,class_id,sender_id,body,status) VALUES (1,'mock','fixture','fixture',1,'fixture','fixture','HANDOFF')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO tickets(id,event_id,class_id,reason,status) VALUES (1,1,1,'INCOMPLETE_EVIDENCE','OPEN')"
                )
            )
        migrate(engine)
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT status, version, assigned_teacher_id FROM tickets")
            ).one() == ("OPEN", 1, None)
            assert connection.scalar(text("SELECT status FROM events")) == "HANDOFF"
        migrate(engine, "0008_contextual_teaching", downgrade=True)
        assert not inspect(engine).has_table("staff_actors", schema=engine.rag_test_schema)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT status FROM tickets")) == "OPEN"
        migrate(engine)
        assert inspect(engine).has_table("teacher_reply_outbox", schema=engine.rag_test_schema)
