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
from app.retrieval.service import resolve_authorized_scope


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
def staff_system(generation_system, tmp_path):
    from fastapi.testclient import TestClient
    from teacher_faq_helpers import build_staff_system

    from app.main import app

    system = build_staff_system(generation_system, tmp_path)
    try:
        with TestClient(app) as client:
            yield client, system
    finally:
        app.dependency_overrides.clear()


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


@pytest.fixture
def generation_system(authorized_contexts):

    from generation_samples import TEXT
    from test_hybrid_service import QueryEmbedding
    from test_ingestion_db import section

    from app.generation.evidence import EvidenceGate
    from app.generation.pipeline import AnswerPipeline
    from app.knowledge.store import KnowledgeStore
    from app.llm.provider import FakeLLM
    from app.retrieval.service import HybridRetrievalService

    sessions, contexts = authorized_contexts
    embeddings = QueryEmbedding()
    store = KnowledgeStore(sessions, embeddings)
    store.ingest(
        [
            section(
                TEXT,
                doc_id="AUTO",
                section_id="AUTO-03",
                class_scope="AUTO-N8N-01",
            )
        ]
    )
    store.ingest(
        [
            section(
                "SUMIFS: tính tổng theo nhiều điều kiện. Kiểm tra vùng dữ liệu.",
                doc_id="GS",
            )
        ]
    )
    retrieval = HybridRetrievalService(sessions, embeddings)
    gate = EvidenceGate(sessions)
    pipeline = AnswerPipeline(retrieval, gate, FakeLLM())
    return sessions, contexts, store, pipeline
