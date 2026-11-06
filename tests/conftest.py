"""Real pgvector integration in disposable schemas of a dedicated test database."""

from contextlib import contextmanager
import os
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema


def migrate(engine, revision="head"):
    config = Config("alembic.ini")

    with engine.connect() as connection:
        config.attributes["connection"] = connection
        config.attributes["version_table_schema"] = engine.rag_test_schema
        command.upgrade(config, revision)


@pytest.fixture(scope="session")
def rag_database_factory():
    url = os.getenv("TEST_DATABASE_URL")

    if not url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration")

    database = make_url(url).database or ""
    if not any(marker in database.lower() for marker in ("test", "smoke")):
        pytest.fail("Use a dedicated database whose name contains test or smoke")

    admin = create_engine(url)

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
