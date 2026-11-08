"""Exact pgvector diagnostic for operators; this is not a student retrieval API."""

import argparse
import json

from app.db import get_session_factory
from app.knowledge.embedding_contract import MODEL_KEY
from app.knowledge.embeddings import EmbeddingService
from app.knowledge.source_manifest import CLASS_CODES
from app.retrieval.service import dense_candidates


def search(session, authorized_class: str, query_vector: list[float], top_k: int = 3) -> list[dict]:
    """The caller supplies a trusted class binding, never an arbitrary student claim."""
    if authorized_class not in CLASS_CODES or not 1 <= top_k <= 5:
        raise ValueError("Expected an authorized demo class and top-k between 1 and 5")

    return [
        {
            **candidate.chunk.model_dump(
                include={
                    "doc_id",
                    "section_id",
                    "source_name",
                    "locator",
                    "version",
                    "external_version",
                    "class_scope",
                }
            ),
            "distance": candidate.score,
        }
        for candidate in dense_candidates(session, authorized_class, query_vector, top_k)
    ]


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
