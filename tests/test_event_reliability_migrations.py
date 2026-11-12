from datetime import UTC, datetime

from conftest import migrate
from sqlalchemy import select, text
from sqlalchemy.orm import Session


def test_upgrade_and_downgrade_preserve_existing_outcome(rag_database_factory):
    with rag_database_factory(revision="0006_grounded_generation") as engine:
        with Session(engine) as session, session.begin():
            from app.models import Answer, Classroom

            classroom = Classroom(code="MIG-EVENT", name="Migration event")
            session.add(classroom)
            session.flush()
            event_id = session.scalar(
                text(
                    "INSERT INTO events (channel, external_event_id, group_id, class_id, "
                    "sender_id, body, status, attempts) VALUES "
                    "('mock', 'migration-event', 'migration-group', :class_id, "
                    "'sender', '/hoi migration check', 'ANSWERED', 1) RETURNING id"
                ),
                {"class_id": classroom.id},
            )
            processing_id = session.scalar(
                text(
                    "INSERT INTO events (channel, external_event_id, group_id, class_id, "
                    "sender_id, body, status, attempts) VALUES "
                    "('mock', 'orphan-processing', 'migration-group', :class_id, "
                    "'sender', '/hoi orphan', 'PROCESSING', 1) RETURNING id"
                ),
                {"class_id": classroom.id},
            )
            session.add(
                Answer(
                    event_id=event_id,
                    decision="ANSWER",
                    reply_text="Kept through migration.",
                    source_chunks=[],
                )
            )

        migrate(engine, "head")
        with Session(engine) as session:
            from app.models import OutboxMessage

            event = session.execute(
                text("SELECT status, lease_token FROM events WHERE id = :event_id"),
                {"event_id": event_id},
            ).one()
            answer = session.scalar(select(Answer).where(Answer.event_id == event_id))
            assert event.status == "ANSWERED"
            assert event.lease_token is None
            assert answer.reply_text == "Kept through migration."
            processing = session.execute(
                text("SELECT lease_expires_at FROM events WHERE id = :event_id"),
                {"event_id": processing_id},
            ).one()
            assert processing.lease_expires_at > datetime.now(UTC)
            assert session.query(OutboxMessage).count() == 0

        migrate(engine, "0006_grounded_generation", downgrade=True)
        migrate(engine, "head")
        with Session(engine) as session:
            assert (
                session.scalar(
                    text("SELECT status FROM events WHERE id = :event_id"), {"event_id": event_id}
                )
                == "ANSWERED"
            )
            assert session.scalar(select(Answer).where(Answer.event_id == event_id)).reply_text == (
                "Kept through migration."
            )
