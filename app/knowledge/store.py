"""Bounded ingestion with short staging/activation transactions and urgent revoke."""

import hashlib
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import func, select, text, update

from app.config import Settings, get_settings
from app.knowledge.chunking import IndexedChunk, chunk_sections, digest
from app.knowledge.embedding_contract import DIMENSION, MODEL_KEY
from app.knowledge.embeddings import validate_vector
from app.knowledge.ingestion import KnowledgeChunk
from app.knowledge.source_manifest import ELIGIBLE_REVIEWS, PARSER_REVISION
from app.models import Document, DocumentChunk

logger = logging.getLogger(__name__)


def _lock(session, logical_key: str) -> None:
    key = int.from_bytes(hashlib.sha256(logical_key.encode()).digest()[:8], signed=True)
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def group_documents(sections: list[KnowledgeChunk]) -> list[list[KnowledgeChunk]]:
    groups = defaultdict(list)

    for section in sections:
        groups[section.doc_id].append(section)

    return list(groups.values())


class KnowledgeStore:
    def __init__(self, session_factory, embeddings, settings: Settings | None = None):
        self.sessions = session_factory
        self.embeddings = embeddings
        self.settings = settings or get_settings()

        if embeddings.model_id != MODEL_KEY or embeddings.dimension != DIMENSION:
            raise ValueError("Embedding service differs from the fixed index model/dimension")

    def ingest(self, sections: list[KnowledgeChunk], force: bool = False) -> dict:
        sections = [KnowledgeChunk.model_validate(section.model_dump()) for section in sections]

        if not sections:
            raise ValueError("Cannot ingest an empty logical document")

        logical_key = sections[0].doc_id

        if any(
            section.doc_id != logical_key or section.version != sections[0].version
            for section in sections
        ):
            raise ValueError("Ingest one logical document/external version at a time")

        identities = [section.canonical_key for section in sections]
        if len(set(identities)) != len(identities):
            raise ValueError("Duplicate canonical source sections")

        if any(len(section.source_hash) != 64 for section in sections):
            raise ValueError("Every section requires a verified source checksum")

        if any(
            section.review_status == "approved_demo" and section.review_decision is None
            for section in sections
        ):
            raise ValueError("Demo approval requires an audited human review decision")

        eligible = [section for section in sections if section.review_status in ELIGIBLE_REVIEWS]

        if not eligible:
            self.revoke(logical_key, "NO_ELIGIBLE_CONTENT")
            return {"doc_id": logical_key, "status": "pending", "chunks": 0}

        if len({section.data_origin for section in eligible}) != 1:
            raise ValueError("A logical document must have one data origin")

        chunks = chunk_sections(
            eligible,
            self.embeddings.tokenizer,
            self.settings.chunk_target_tokens,
            self.settings.chunk_overlap_tokens,
        )
        hashes = sorted({section.source_hash for section in sections})
        checksum = hashes[0] if len(hashes) == 1 else digest(json.dumps(hashes))
        fingerprint = digest(
            json.dumps(
                {
                    "model": MODEL_KEY,
                    "parser": PARSER_REVISION,
                    "target": self.settings.chunk_target_tokens,
                    "overlap": self.settings.chunk_overlap_tokens,
                    "sections": [section.model_dump(mode="json") for section in sections],
                    "chunks": [chunk.chunk_id for chunk in chunks],
                },
                sort_keys=True,
                ensure_ascii=False,
            )
        )
        stage = self._stage(logical_key, sections[0], chunks, checksum, fingerprint, force)

        if stage["status"] == "skipped":
            return stage

        document_id = stage["document_id"]

        try:
            batch_size = self.settings.embedding_batch_size

            for offset in range(0, len(chunks), batch_size):
                batch = chunks[offset : offset + batch_size]
                # No session/transaction is held while the model runs.
                vectors = self.embeddings.encode_passages([chunk.content for chunk in batch])

                if len(vectors) != len(batch):
                    raise ValueError("Embedding count differs from staged chunk count")

                vectors = [validate_vector(vector) for vector in vectors]
                self._insert_batch(document_id, logical_key, offset, batch, vectors)
                logger.info(
                    "staged doc_id=%s version=%s vectors=%s",
                    logical_key,
                    stage["version"],
                    offset + len(batch),
                )

            self._activate(document_id, logical_key, chunks)

        except Exception as error:
            self._fail(document_id, type(error).__name__)
            # Provider/DB exception messages can contain source content or SQL parameters.
            raise RuntimeError(
                f"Ingestion failed for {logical_key}; staged version remains inactive"
            ) from None

        return {**stage, "status": "ready", "chunks": len(chunks), "checksum": checksum}

    def _stage(self, logical_key, source, chunks, checksum, fingerprint, force):
        with self.sessions.begin() as session:
            _lock(session, logical_key)
            history = list(
                session.scalars(
                    select(Document)
                    .where(Document.logical_key == logical_key)
                    .order_by(Document.version)
                )
            )

            if any(
                document.index_status == "REVOKED" and document.index_fingerprint == fingerprint
                for document in history
            ):
                raise ValueError(
                    "Identical revoked input cannot be reactivated; review a new source/version"
                )

            active = next((document for document in history if document.active), None)

            if active and active.index_fingerprint == fingerprint and not force:
                return {
                    "doc_id": logical_key,
                    "document_id": active.id,
                    "version": active.version,
                    "status": "skipped",
                    "chunks": active.expected_chunks,
                }

            # Scope removals/review demotions take effect immediately, even if embedding fails.
            if active:
                previous_scopes = set(
                    session.execute(
                        select(DocumentChunk.section_id, DocumentChunk.class_scope).where(
                            DocumentChunk.document_id == active.id
                        )
                    )
                )
                incoming_scopes = {(chunk.section_id, chunk.class_scope) for chunk in chunks}

                if not previous_scopes <= incoming_scopes:
                    for previous in history:
                        previous.active = False
                        previous.index_status = "REVOKED"
                        previous.revoked_at = _now()
                        previous.revocation_reason = "SCOPE_OR_REVIEW_REDUCED"

            version = max((document.version for document in history), default=0) + 1
            document = Document(
                logical_key=logical_key,
                source_name=source.source_name,
                version=version,
                external_version=source.version,
                checksum=checksum,
                active=False,
                index_status="STAGING",
                review_status="approved_demo"
                if any(chunk.review_status == "approved_demo" for chunk in chunks)
                else "provided_sample",
                data_origin=chunks[0].data_origin,
                embedding_model=MODEL_KEY,
                embedding_dimension=DIMENSION,
                parser_revision=PARSER_REVISION,
                index_fingerprint=fingerprint,
                expected_chunks=len(chunks),
                review_audit=[
                    {
                        "section_id": chunk.section_id,
                        **chunk.review_decision.model_dump(mode="json"),
                    }
                    for chunk in chunks
                    if chunk.review_decision is not None
                ],
            )
            session.add(document)
            session.flush()

            return {
                "doc_id": logical_key,
                "document_id": document.id,
                "version": version,
                "status": "staging",
            }

    def _insert_batch(self, document_id, logical_key, offset, chunks, vectors):
        with self.sessions.begin() as session:
            _lock(session, logical_key)
            document = session.get(Document, document_id)

            if document.index_status != "STAGING":
                raise ValueError("Staged document was revoked or superseded")

            for index, (chunk, vector) in enumerate(zip(chunks, vectors), start=offset):
                session.add(
                    DocumentChunk(
                        document_id=document_id,
                        chunk_index=index,
                        section_chunk_index=chunk.chunk_index,
                        chunk_id=chunk.chunk_id,
                        section_id=chunk.section_id,
                        class_scope=chunk.class_scope,
                        content=chunk.content,
                        source_name=chunk.source_name,
                        page_or_section=chunk.section_id,
                        locator=chunk.locator,
                        content_hash=chunk.content_hash,
                        source_checksum=chunk.source_hash,
                        review_status=chunk.review_status,
                        data_origin=chunk.data_origin,
                        topics=list(chunk.topics),
                        token_count=chunk.token_count,
                        embedding=vector,
                    )
                )

    def _activate(self, document_id: int, logical_key: str, expected: list[IndexedChunk]):
        with self.sessions.begin() as session:
            _lock(session, logical_key)
            document = session.get(Document, document_id)
            latest_version = session.scalar(
                select(func.max(Document.version)).where(Document.logical_key == logical_key)
            )

            if document.index_status != "STAGING" or document.version != latest_version:
                raise ValueError("Staged document was revoked or superseded")

            stored = list(
                session.scalars(
                    select(DocumentChunk)
                    .where(DocumentChunk.document_id == document_id)
                    .order_by(DocumentChunk.chunk_index)
                )
            )

            if len(stored) != len(expected) or document.expected_chunks != len(expected):
                raise ValueError("Staged chunk count mismatch")

            for actual, wanted in zip(stored, expected):
                if (
                    actual.chunk_id != wanted.chunk_id
                    or actual.content_hash != digest(actual.content)
                    or actual.content_hash != wanted.content_hash
                    or actual.source_checksum != wanted.source_hash
                    or actual.class_scope != wanted.class_scope
                    or actual.section_id != wanted.section_id
                    or actual.locator != wanted.locator
                    or actual.source_name != wanted.source_name
                    or actual.review_status != wanted.review_status
                    or actual.review_status not in ELIGIBLE_REVIEWS
                ):
                    raise ValueError("Staged chunk metadata mismatch")

                validate_vector(actual.embedding)

            session.execute(
                update(Document)
                .where(Document.logical_key == logical_key, Document.active.is_(True))
                .values(active=False)
            )
            session.flush()
            document.index_status = "READY"
            document.indexed_at = _now()
            document.active = True

    def _fail(self, document_id: int, failure_code: str):
        with self.sessions.begin() as session:
            document = session.get(Document, document_id, with_for_update=True)

            if document and document.index_status == "STAGING":
                document.index_status = "FAILED"
                document.failure_code = failure_code[:80]

    def revoke(self, logical_key: str, reason: str) -> int:
        if not reason.strip() or len(reason) > 255:
            raise ValueError("Revocation requires a reason of 1 to 255 characters")

        with self.sessions.begin() as session:
            _lock(session, logical_key)
            result = session.execute(
                update(Document)
                .where(Document.logical_key == logical_key)
                .values(
                    active=False,
                    index_status="REVOKED",
                    revoked_at=_now(),
                    revocation_reason=reason,
                )
            )

            return result.rowcount

    def status(self) -> list[dict]:
        with self.sessions() as session:
            return [
                {
                    "doc_id": document.logical_key,
                    "document_id": document.id,
                    "version": document.version,
                    "external_version": document.external_version,
                    "active": document.active,
                    "status": document.index_status,
                    "review": document.review_status,
                    "expected_chunks": document.expected_chunks,
                    "stored_chunks": session.scalar(
                        select(func.count())
                        .select_from(DocumentChunk)
                        .where(DocumentChunk.document_id == document.id)
                    ),
                    "model": document.embedding_model,
                    "dimension": document.embedding_dimension,
                    "checksum": document.checksum,
                    "failure_code": document.failure_code,
                }
                for document in session.scalars(
                    select(Document).order_by(Document.logical_key, Document.version)
                )
            ]
