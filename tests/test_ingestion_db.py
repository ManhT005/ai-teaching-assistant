"""Lifecycle checks use deterministic vectors, never a retrieval-quality claim."""

import hashlib
import math

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.knowledge.chunking import digest
from app.knowledge.embedding_contract import DIMENSION, MODEL_KEY
from app.knowledge.ingestion import KnowledgeChunk
from app.knowledge.store import KnowledgeStore
from app.models import Document, DocumentChunk


class WordTokenizer:
    def encode(self, text, add_special_tokens=True):
        return text.split() + ([0, 0] if add_special_tokens else [])


class FakeEmbedding:
    model_id = MODEL_KEY
    dimension = DIMENSION
    tokenizer = WordTokenizer()

    def encode_passages(self, texts):
        vectors = []
        for passage in texts:
            raw = hashlib.shake_256(passage.encode()).digest(DIMENSION)
            values = [byte / 255 - 0.5 for byte in raw]
            norm = math.sqrt(sum(value * value for value in values))
            vectors.append([value / norm for value in values])
        return vectors


def section(content="GS-02 Tổng\nSUMIFS điều kiện ngày", **changes):
    fields = dict(
        record_id="GS-02",
        doc_id="DOC-GS-01",
        section_id="GS-02",
        version="TEST-1.0",
        class_scope="GS-KPI-01",
        content=content,
        source_name="sample.docx",
        locator="block 1",
        source_hash=digest(content),
        data_origin="provided_sample",
        review_status="provided_sample",
    )
    return KnowledgeChunk(**{**fields, **changes})


@pytest.fixture
def store(rag_engine):
    return KnowledgeStore(
        sessionmaker(rag_engine, expire_on_commit=False),
        FakeEmbedding(),
        Settings(embedding_batch_size=1),
    )


def test_ingest_skip_reindex_and_internal_version_with_same_external_version(store):
    first = store.ingest([section()])
    assert first["status"] == "ready" and first["version"] == 1
    assert store.ingest([section()])["status"] == "skipped"
    changed = store.ingest([section("GS-02 Tổng\nCông thức mới")])
    assert changed["version"] == 2

    with store.sessions() as session:
        documents = list(session.scalars(select(Document).order_by(Document.version)))
        assert [document.active for document in documents] == [False, True]
        assert {document.external_version for document in documents} == {"TEST-1.0"}
        assert session.scalar(select(func.count()).select_from(DocumentChunk)) == 2

    forced = store.ingest([section("GS-02 Tổng\nCông thức mới")], force=True)
    assert forced["version"] == 3


def test_embedding_failure_after_first_batch_keeps_old_version_active(store, monkeypatch):
    first = store.ingest([section()])
    encode = store.embeddings.encode_passages
    calls = 0

    def fail_second_batch(texts):
        nonlocal calls
        calls += 1

        if calls == 2:
            raise RuntimeError("private course text must not reach logs")

        # No checked-out DB connection is held during embedding.
        assert store.sessions.kw["bind"].pool.checkedout() == 0
        return encode(texts)

    monkeypatch.setattr(store.embeddings, "encode_passages", fail_second_batch)

    with pytest.raises(RuntimeError, match="remains inactive") as error:
        store.ingest([section("GS-02 Tổng\n" + "nội dung mới " * 300)])

    assert "private course" not in str(error.value)
    rows = store.status()
    assert rows[0]["active"] is True and rows[0]["document_id"] == first["document_id"]
    assert rows[1]["status"] == "FAILED" and rows[1]["stored_chunks"] == 1


def test_insert_failure_and_activation_validation_rollback(store, monkeypatch):
    store.ingest([section()])
    insert_batch = store._insert_batch

    def corrupt_content(*args):
        insert_batch(*args)
        with store.sessions.begin() as session:
            session.execute(
                text("UPDATE document_chunks SET content = 'corrupt' WHERE document_id = :id"),
                {"id": args[0]},
            )

    monkeypatch.setattr(store, "_insert_batch", corrupt_content)

    with pytest.raises(RuntimeError):
        store.ingest([section("GS-02 Tổng\nBản cập nhật")])

    assert [row["active"] for row in store.status()] == [True, False]
    assert store.status()[-1]["status"] == "FAILED"


def test_activation_database_failure_restores_old_active_version(store):
    store.ingest([section()])

    with store.sessions.begin() as session:
        session.execute(
            text("""
            CREATE FUNCTION reject_new_activation() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.active AND NEW.version > 1 THEN
                    RAISE EXCEPTION 'activation failure for rollback test';
                END IF;
                RETURN NEW;
            END;
            $$
        """)
        )
        session.execute(
            text("""
            CREATE TRIGGER fail_activation BEFORE UPDATE ON documents
            FOR EACH ROW EXECUTE FUNCTION reject_new_activation()
        """)
        )

    with pytest.raises(RuntimeError, match="remains inactive"):
        store.ingest([section("GS-02 Tổng\nVersion changed")])

    rows = store.status()
    assert [row["active"] for row in rows] == [True, False]
    assert rows[1]["status"] == "FAILED"
    assert rows[1]["stored_chunks"] == rows[1]["expected_chunks"]


def test_pending_initial_and_review_demotion_never_remain_active(store):
    pending = section(review_status="pending", data_origin="synthetic_demo")
    assert store.ingest([pending])["status"] == "pending"
    assert store.status() == []
    store.ingest([section()])
    store.ingest([pending])
    assert all(not row["active"] and row["status"] == "REVOKED" for row in store.status())


def test_revoke_blocks_inflight_activation_and_identical_reingest(store, monkeypatch):
    store.ingest([section()])
    assert store.revoke("DOC-GS-01", "operator emergency") == 1

    with pytest.raises(ValueError, match="revoked"):
        store.ingest([section()])

    encode = store.embeddings.encode_passages

    def revoke_while_embedding(texts):
        store.revoke("DOC-GS-01", "revoke during staging")
        return encode(texts)

    monkeypatch.setattr(store.embeddings, "encode_passages", revoke_while_embedding)

    with pytest.raises(RuntimeError):
        store.ingest([section("GS-02 Tổng\nBản khác")])

    assert all(not row["active"] and row["status"] == "REVOKED" for row in store.status())


def test_wrong_model_and_invalid_vectors_cannot_activate(store, monkeypatch):
    monkeypatch.setattr(store.embeddings, "model_id", "another-model")

    with pytest.raises(ValueError, match="model/dimension"):
        KnowledgeStore(store.sessions, store.embeddings)

    monkeypatch.setattr(store.embeddings, "model_id", MODEL_KEY)
    monkeypatch.setattr(
        store.embeddings, "encode_passages", lambda texts: [[1, 0, 0] for _ in texts]
    )

    with pytest.raises(RuntimeError):
        store.ingest([section()])

    assert store.status()[0]["status"] == "FAILED"
    assert store.status()[0]["stored_chunks"] == 0


def test_database_rejects_duplicate_chunk_and_incompatible_model(store):
    result = store.ingest([section()])

    with pytest.raises(IntegrityError), store.sessions.begin() as session:
        session.add(
            DocumentChunk(
                document_id=result["document_id"],
                chunk_index=0,
                class_scope="COMMON",
                content="duplicate",
            )
        )

    with pytest.raises(IntegrityError), store.sessions.begin() as session:
        session.add(
            Document(logical_key="other", source_name="x", version=1, embedding_model="wrong")
        )


def test_approved_demo_requires_and_persists_review_audit(store):
    approved = section(review_status="approved_demo", data_origin="synthetic_demo")

    with pytest.raises(ValueError, match="human review"):
        store.ingest([approved])

    from app.knowledge.ingestion import ReviewDecision

    decision = ReviewDecision(
        reviewer="test-reviewer",
        reviewed_at="2026-11-06T20:00:00+07:00",
        reason="In-memory approval test only",
    )
    result = store.ingest([approved.model_copy(update={"review_decision": decision})])

    with store.sessions() as session:
        document = session.get(Document, result["document_id"])
        assert document.review_audit[0]["reviewer"] == "test-reviewer"
        assert document.review_audit[0]["section_id"] == "GS-02"


def test_scope_reduction_revokes_previous_version_even_when_embedding_fails(store, monkeypatch):
    first = section()
    second = section("GS-04 Nhập\nIMPORTRANGE", section_id="GS-04", record_id="GS-04")
    store.ingest([first, second])

    def fail(texts):
        raise RuntimeError("embedding failure")

    monkeypatch.setattr(store.embeddings, "encode_passages", fail)

    with pytest.raises(RuntimeError):
        store.ingest([first, second.model_copy(update={"review_status": "pending"})])

    assert not any(row["active"] for row in store.status())
    assert store.status()[0]["status"] == "REVOKED"


def test_newer_staging_prevents_older_activation(store, monkeypatch):
    store.ingest([section()])
    original_activate = store._activate

    def stage_newer(document_id, logical_key, expected):
        store._stage(logical_key, section(), expected, "a" * 64, "b" * 64, False)
        original_activate(document_id, logical_key, expected)

    monkeypatch.setattr(store, "_activate", stage_newer)

    with pytest.raises(RuntimeError):
        store.ingest([section("GS-02 Tổng\nCập nhật")])

    assert store.status()[0]["active"] is True
    assert not any(row["active"] for row in store.status()[1:])
