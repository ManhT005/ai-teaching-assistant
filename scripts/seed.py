"""Idempotent seed for the two demo classes and local mock bindings."""
from sqlalchemy import select
from app.db import get_session_factory
from app.models import Classroom, ChannelBinding

DEMO_CLASSES = (
    ("GS-KPI-01", "Google Sheets KPI Dashboard", "mock-google-sheets"),
    ("AUTO-N8N-01", "AI Automation n8n", "mock-ai-automation"),
)


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
