"""Real pgvector integration in disposable schemas of a dedicated test database."""

import os
from contextlib import contextmanager
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from app.models import ChannelBinding, Classroom
from app.retrieval import resolve_authorized_scope


def migrate(engine, revision="head", *, downgrade=False):
    config = Config("alembic.ini")

    with engine.connect() as connection:
        config.attributes["connection"] = connection
        config.attributes["version_table_schema"] = engine.rag_test_schema
        operation = command.downgrade if downgrade else command.upgrade
        operation(config, revision)


@pytest.fixture(scope="session")
def rag_database_factory():
    url = os.getenv("TEST_DATABASE_URL")

    if not url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration")

    database = make_url(url).database or ""
    if not any(marker in database.lower() for marker in ("test", "smoke")):
        pytest.fail("Use a dedicated database whose name contains test or smoke")

    admin = create_engine(url)

    with admin.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"))

    @contextmanager
    def factory(revision="head"):
        schema = "rag_test_" + uuid4().hex

        with admin.begin() as connection:
            connection.execute(CreateSchema(schema))

        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema},public"})
        engine.rag_test_schema = schema

        try:
            migrate(engine, revision)
            yield engine
        finally:
            engine.dispose()
            with admin.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))

    yield factory
    admin.dispose()


@pytest.fixture
def rag_engine(rag_database_factory):
    with rag_database_factory() as engine:
        yield engine


@pytest.fixture
def authorized_contexts(rag_engine):
    from sqlalchemy.orm import sessionmaker

    sessions = sessionmaker(rag_engine, expire_on_commit=False)

    with sessions.begin() as session:
        gs = Classroom(code="GS-KPI-01", name="GS")
        auto = Classroom(code="AUTO-N8N-01", name="AUTO")
        session.add_all([gs, auto])
        session.flush()
        session.add_all(
            [
                ChannelBinding(channel="mock", group_id="mock-google-sheets", class_id=gs.id),
                ChannelBinding(channel="mock", group_id="mock-ai-automation", class_id=auto.id),
            ]
        )

    with sessions() as session:
        contexts = {
            code: resolve_authorized_scope(session, channel="mock", group_id=group)
            for code, group in [
                ("GS-KPI-01", "mock-google-sheets"),
                ("AUTO-N8N-01", "mock-ai-automation"),
            ]
        }

    return sessions, contexts
