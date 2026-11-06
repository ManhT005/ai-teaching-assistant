"""Operator-only demo ingestion. Never accepts a student upload or class grant."""

import argparse
import json
import logging
from collections import Counter
from pathlib import Path

from app.chunking import chunk_sections
from app.config import get_settings
from app.db import get_session_factory
from app.embeddings import EmbeddingService
from app.knowledge_store import KnowledgeStore, group_documents
from app.source_manifest import ELIGIBLE_REVIEWS, load_manifest


def inspect_manifest(path: Path, embeddings: EmbeddingService) -> dict:
    sections = load_manifest(path)
    settings = embeddings.settings
    chunks = chunk_sections(
        sections,
        embeddings.tokenizer,
        settings.chunk_target_tokens,
        settings.chunk_overlap_tokens,
    )

    return {
        "mode": "DEMO ONLY; inspect does not load weights or write to DB",
        "sections": len(sections),
        "chunks": len(chunks),
        "eligible_chunks": sum(chunk.review_status in ELIGIBLE_REVIEWS for chunk in chunks),
        "chunks_by_scope": dict(Counter(chunk.class_scope for chunk in chunks)),
        "max_input_tokens": max(chunk.token_count for chunk in chunks),
        "sources": [
            {
                "doc_id": section.doc_id,
                "section_id": section.section_id,
                "scope": section.class_scope,
                "review": section.review_status,
                "checksum": section.source_hash,
                "locator": section.locator,
            }
            for section in sections
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    for command in ("inspect", "ingest"):
        subparser = commands.add_parser(command)
        subparser.add_argument("--manifest", type=Path, required=True)

        if command == "ingest":
            subparser.add_argument(
                "--force", action="store_true", help="Reindex unchanged eligible input"
            )

    commands.add_parser("status")
    revoke = commands.add_parser("revoke", aliases=["deactivate"])
    revoke.add_argument("--doc-id", required=True)
    revoke.add_argument("--reason", required=True)
    args = parser.parse_args()
    logging.basicConfig(level=get_settings().log_level, format="%(levelname)s %(message)s")
    embeddings = EmbeddingService()

    try:
        if args.command == "inspect":
            result = inspect_manifest(args.manifest, embeddings)
        else:
            store = KnowledgeStore(get_session_factory(), embeddings)

            if args.command == "ingest":
                sections = load_manifest(args.manifest)
                result = [
                    store.ingest(group, force=args.force) for group in group_documents(sections)
                ]
            elif args.command == "status":
                result = store.status()
            else:
                result = {
                    "doc_id": args.doc_id,
                    "revoked_versions": store.revoke(args.doc_id, args.reason),
                }

    except Exception as error:
        # Keep raw source/SQL/provider messages out of terminal logs.
        parser.exit(
            1,
            f"{type(error).__name__}: ingestion command failed; check manifest, cached model and database configuration\n",
        )

    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
