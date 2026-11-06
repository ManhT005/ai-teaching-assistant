"""Scoped retrieval primitives; only server-resolved bindings authorize a search."""

from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.embedding_contract import DIMENSION, MODEL_KEY
from app.embeddings import validate_vector
from app.lexical_search import lexical_tsquery
from app.models import ChannelBinding, Classroom, Document, DocumentChunk
from app.source_manifest import CLASS_CODES, ELIGIBLE_REVIEWS

_SCOPE_PROOF = object()


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
