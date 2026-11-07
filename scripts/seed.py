"""Idempotent seed for the two demo classes and local mock bindings."""
from sqlalchemy import select

from app.db import get_session_factory
from app.mock_channel import DEMO_CLASSES
from app.models import ChannelBinding, Classroom


def seed() -> None:
    with get_session_factory()() as session:
        for code, name, group_id in DEMO_CLASSES:
            classroom = session.scalar(select(Classroom).where(Classroom.code == code))
            if classroom is None:
                classroom = Classroom(code=code, name=name, bot_enabled=True)
                session.add(classroom)
                session.flush()
            binding = session.scalar(select(ChannelBinding).where(ChannelBinding.channel == "mock", ChannelBinding.group_id == group_id))
            if binding is None:
                session.add(ChannelBinding(channel="mock", group_id=group_id, class_id=classroom.id, active=True))
        session.commit()
    print("Demo classes and mock channel bindings seeded.")


if __name__ == "__main__":
    seed()

