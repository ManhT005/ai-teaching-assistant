"""Scoped retrieval primitives; only server-resolved bindings authorize a search."""

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from time import perf_counter

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.embedding_contract import DIMENSION, MODEL_KEY
from app.embeddings import validate_vector
from app.lexical_search import extract_technical_terms, lexical_tsquery
from app.models import ChannelBinding, Classroom, Document, DocumentChunk
from app.rank_fusion import reciprocal_rank_fusion
from app.source_manifest import CLASS_CODES, ELIGIBLE_REVIEWS

_SCOPE_PROOF = object()
logger = logging.getLogger(__name__)


class ScopeAuthorizationError(PermissionError):
    """The trusted channel binding is missing, disabled, stale or inconsistent."""


@dataclass(frozen=True)
class AuthorizedScope:
    binding_id: int
    class_id: int
    class_code: str
    channel: str
    group_id: str
    _proof: object = field(repr=False, compare=False)


def resolve_authorized_scope(
    session: Session,
    *,
    channel: str,
    group_id: str,
) -> AuthorizedScope:
    """Call after authenticating the channel, or from a DB-privileged operator CLI.

    Knowing a group name does not prove student membership. This function is an
    internal binding lookup, never a public authentication or class-selection API.
    """
    row = session.execute(
        select(ChannelBinding, Classroom)
        .join(Classroom, Classroom.id == ChannelBinding.class_id)
        .where(
            ChannelBinding.channel == channel,
            ChannelBinding.group_id == group_id,
            ChannelBinding.active.is_(True),
            Classroom.bot_enabled.is_(True),
        )
    ).one_or_none()

    if row is None or row.Classroom.code not in CLASS_CODES:
        raise ScopeAuthorizationError("No enabled classroom binding for the trusted channel")

    return AuthorizedScope(
        binding_id=row.ChannelBinding.id,
        class_id=row.Classroom.id,
        class_code=row.Classroom.code,
        channel=channel,
        group_id=group_id,
        _proof=_SCOPE_PROOF,
    )


def validate_authorized_scope(session: Session, context: AuthorizedScope) -> None:
    if not isinstance(context, AuthorizedScope) or context._proof is not _SCOPE_PROOF:
        raise ScopeAuthorizationError("Search requires a server-resolved authorization scope")

    current = resolve_authorized_scope(session, channel=context.channel, group_id=context.group_id)

    if current != context:
        raise ScopeAuthorizationError("Classroom binding changed; resolve authorization again")


def eligible_chunk_predicates(authorized_class: str) -> tuple:
    """One SQL allowlist for dense, lexical and legacy operator smoke queries."""
    if authorized_class not in CLASS_CODES:
        raise ScopeAuthorizationError("Unknown authorized classroom")

    return (
        Document.active.is_(True),
        Document.index_status == "READY",
        Document.revoked_at.is_(None),
        Document.review_status.in_(ELIGIBLE_REVIEWS),
        Document.embedding_model == MODEL_KEY,
        Document.embedding_dimension == DIMENSION,
        DocumentChunk.review_status.in_(ELIGIBLE_REVIEWS),
        DocumentChunk.class_scope.in_((authorized_class, "COMMON")),
        DocumentChunk.embedding.is_not(None),
    )


class RetrievedChunk(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    row_id: int = Field(gt=0)
    chunk_id: str = Field(min_length=1)
    document_id: int = Field(gt=0)
    doc_id: str = Field(min_length=1)
    version: int = Field(gt=0)
    external_version: str
    section_id: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    class_scope: str
    content: str = Field(min_length=1)


@dataclass(frozen=True)
class Candidate:
    chunk: RetrievedChunk
    score: float


def scoped_candidate_query(authorized_class: str):
    return (
        select(
            DocumentChunk.id.label("row_id"),
            DocumentChunk.chunk_id,
            DocumentChunk.document_id,
            Document.logical_key.label("doc_id"),
            Document.version,
            Document.external_version,
            DocumentChunk.section_id,
            DocumentChunk.source_name,
            DocumentChunk.locator,
            DocumentChunk.class_scope,
            DocumentChunk.content,
        )
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(*eligible_chunk_predicates(authorized_class))
    )


def candidate_rows(session: Session, statement) -> list[Candidate]:
    candidates = []

    for row in session.execute(statement).mappings():
        fields = dict(row)
        score = float(fields.pop("score"))
        candidates.append(Candidate(chunk=RetrievedChunk.model_validate(fields), score=score))

    return candidates


def validate_candidate_limit(limit: int) -> None:
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("Candidate limit must be between 1 and 50")


def dense_candidates(
    session: Session,
    authorized_class: str,
    query_vector: list[float],
    limit: int = 10,
) -> list[Candidate]:
    validate_candidate_limit(limit)
    vector = validate_vector(query_vector)
    distance = DocumentChunk.embedding.cosine_distance(vector).label("score")
    statement = (
        scoped_candidate_query(authorized_class)
        .add_columns(distance)
        .order_by(distance, DocumentChunk.id)
        .limit(limit)
    )

    return candidate_rows(session, statement)


def lexical_candidates(
    session: Session,
    authorized_class: str,
    terms: tuple[str, ...],
    limit: int = 10,
) -> list[Candidate]:
    validate_candidate_limit(limit)
    statement = scoped_candidate_query(authorized_class)

    if not terms:
        return []

    query = lexical_tsquery(terms)
    rank = func.ts_rank_cd(DocumentChunk.search_vector, query).label("score")
    statement = (
        statement.add_columns(rank)
        .where(DocumentChunk.search_vector.op("@@")(query))
        .order_by(rank.desc(), DocumentChunk.id)
        .limit(limit)
    )

    return candidate_rows(session, statement)


class RetrievalHit(RetrievedChunk):
    dense_rank: int | None
    dense_distance: float | None
    lexical_rank: int | None
    lexical_score: float | None
    rrf_score: float


class CandidateDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    row_id: int
    chunk_id: str
    doc_id: str
    section_id: str
    version: int
    class_scope: str
    score: float


class RetrievalTimings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    encode_ms: float
    scope_ms: float
    dense_sql_ms: float
    lexical_sql_ms: float
    fusion_ms: float
    total_ms: float


class RetrievalDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lexical_terms: tuple[str, ...]
    dense: tuple[CandidateDiagnostic, ...]
    lexical: tuple[CandidateDiagnostic, ...]
    rrf_k: int
    timings: RetrievalTimings


class RetrievalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = MODEL_KEY
    authorized_class: str
    top_k: int
    hits: tuple[RetrievalHit, ...]
    diagnostics: RetrievalDiagnostics


def normalize_question(question: str) -> str:
    if not isinstance(question, str) or not 1 <= len(question) <= 1000:
        raise ValueError("Question must contain between 1 and 1000 characters")

    if any(
        unicodedata.category(character).startswith("C") and not character.isspace()
        for character in question
    ):
        raise ValueError("Question contains unsupported control characters")

    normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFC", question)).strip()

    if not normalized:
        raise ValueError("Question must not be blank")

    return normalized


def _diagnostic(candidate: Candidate) -> CandidateDiagnostic:
    return CandidateDiagnostic(
        **candidate.chunk.model_dump(
            include={"row_id", "chunk_id", "doc_id", "section_id", "version", "class_scope"}
        ),
        score=candidate.score,
    )


def fuse_candidates(
    dense: list[Candidate], lexical: list[Candidate], top_k: int, k: int
) -> tuple[RetrievalHit, ...]:
    dense_by_id = {candidate.chunk.row_id: candidate for candidate in dense}
    lexical_by_id = {candidate.chunk.row_id: candidate for candidate in lexical}

    for row_id in dense_by_id.keys() & lexical_by_id.keys():
        if dense_by_id[row_id].chunk != lexical_by_id[row_id].chunk:
            raise ValueError("Dense and lexical metadata disagree for a chunk row")

    fused = reciprocal_rank_fusion(list(dense_by_id), list(lexical_by_id), k)
    hits = []

    for rank in fused[:top_k]:
        dense_candidate = dense_by_id.get(rank.row_id)
        lexical_candidate = lexical_by_id.get(rank.row_id)
        candidate = dense_candidate or lexical_candidate
        hits.append(
            RetrievalHit(
                **candidate.chunk.model_dump(),
                dense_rank=rank.dense_rank,
                dense_distance=dense_candidate.score if dense_candidate else None,
                lexical_rank=rank.lexical_rank,
                lexical_score=lexical_candidate.score if lexical_candidate else None,
                rrf_score=rank.rrf_score,
            )
        )

    return tuple(hits)


class HybridRetrievalService:
    def __init__(self, session_factory, embeddings, settings: Settings | None = None):
        self.sessions = session_factory
        self.embeddings = embeddings
        self.settings = settings or get_settings()

        if embeddings.model_id != MODEL_KEY or embeddings.dimension != DIMENSION:
            raise ValueError("Retrieval encoder must match the pinned ingestion model")

    def search(self, question: str, context: AuthorizedScope, top_k: int = 3) -> RetrievalResponse:
        started = perf_counter()
        question = normalize_question(question)

        if type(top_k) is not int or not 1 <= top_k <= 5:
            raise ValueError("top_k must be between 1 and 5")

        if not isinstance(context, AuthorizedScope) or context._proof is not _SCOPE_PROOF:
            raise ScopeAuthorizationError("Search requires a server-resolved authorization scope")

        terms = extract_technical_terms(question, self.settings.retrieval_max_lexical_terms)
        encode_started = perf_counter()
        vector = validate_vector(self.embeddings.encode_query(question))
        encoded = perf_counter()

        with self.sessions() as session:
            # One read-only snapshot keeps both legs consistent during reindex/revoke.
            # Encoding above holds no database transaction or checked-out connection.
            session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            session.execute(text("SET TRANSACTION READ ONLY"))
            validate_authorized_scope(session, context)
            scoped = perf_counter()
            dense = dense_candidates(
                session, context.class_code, vector, self.settings.retrieval_dense_candidates
            )
            dense_finished = perf_counter()
            lexical = lexical_candidates(
                session, context.class_code, terms, self.settings.retrieval_lexical_candidates
            )
            lexical_finished = perf_counter()

        fusion_started = perf_counter()
        hits = fuse_candidates(dense, lexical, top_k, self.settings.retrieval_rrf_k)
        fused = perf_counter()
        timings = RetrievalTimings(
            encode_ms=(encoded - encode_started) * 1000,
            scope_ms=(scoped - encoded) * 1000,
            dense_sql_ms=(dense_finished - scoped) * 1000,
            lexical_sql_ms=(lexical_finished - dense_finished) * 1000,
            fusion_ms=(fused - fusion_started) * 1000,
            total_ms=(fused - started) * 1000,
        )
        logger.info(
            "hybrid binding_id=%s dense=%s lexical=%s hits=%s total_ms=%.2f",
            context.binding_id,
            len(dense),
            len(lexical),
            len(hits),
            timings.total_ms,
        )

        return RetrievalResponse(
            authorized_class=context.class_code,
            top_k=top_k,
            hits=hits,
            diagnostics=RetrievalDiagnostics(
                lexical_terms=terms,
                dense=tuple(_diagnostic(candidate) for candidate in dense),
                lexical=tuple(_diagnostic(candidate) for candidate in lexical),
                rrf_k=self.settings.retrieval_rrf_k,
                timings=timings,
            ),
        )
