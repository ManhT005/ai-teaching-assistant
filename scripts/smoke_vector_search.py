"""Exact pgvector diagnostic for operators; this is not a student retrieval API."""

import argparse
import json

from sqlalchemy import select

from app.db import get_session_factory
from app.embedding_contract import DIMENSION, MODEL_KEY
from app.embeddings import EmbeddingService, validate_vector
from app.models import Document, DocumentChunk
from app.source_manifest import CLASS_CODES, ELIGIBLE_REVIEWS


def search(session, authorized_class: str, query_vector: list[float], top_k: int = 3) -> list[dict]:
    """The caller supplies a trusted class binding, never an arbitrary student claim."""
    if authorized_class not in CLASS_CODES or not 1 <= top_k <= 5:
        raise ValueError("Expected an authorized demo class and top-k between 1 and 5")

    vector = validate_vector(query_vector)
    distance = DocumentChunk.embedding.cosine_distance(vector).label("distance")
    statement = (
        select(
            Document.logical_key.label("doc_id"),
            DocumentChunk.section_id,
            DocumentChunk.source_name,
            DocumentChunk.locator,
            Document.version,
            Document.external_version,
            DocumentChunk.class_scope,
            distance,
        )
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(
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
        .order_by(distance, DocumentChunk.id)
        .limit(top_k)
    )

    return [dict(row) for row in session.execute(statement).mappings()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--class", dest="authorized_class", choices=CLASS_CODES, required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--top-k", type=int, choices=range(1, 6), default=3)
    args = parser.parse_args()

    try:
        vector = EmbeddingService().encode_query(args.query)
        with get_session_factory()() as session:
            hits = search(session, args.authorized_class, vector, args.top_k)
    except Exception as error:
        parser.exit(
            1,
            f"{type(error).__name__}: vector smoke failed; check cached model and migrated database\n",
        )

    print(
        json.dumps(
            {"mode": "DEMO ONLY", "model": MODEL_KEY, "hits": hits}, ensure_ascii=True, indent=2
        )
    )


if __name__ == "__main__":
    main()
