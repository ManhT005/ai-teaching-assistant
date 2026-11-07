"""Operator-only hybrid retrieval via a database-resolved mock channel binding."""

import argparse
import json

from app.db import get_session_factory
from app.knowledge.embeddings import EmbeddingService
from app.retrieval.service import (
    HybridRetrievalService,
    ScopeAuthorizationError,
    resolve_authorized_scope,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", choices=["mock"], default="mock")
    parser.add_argument(
        "--group", required=True, help="Existing operator-selected mock group binding"
    )
    parser.add_argument("--query", required=True)
    parser.add_argument("--top-k", type=int, choices=range(1, 6), default=3)
    parser.add_argument(
        "--include-content",
        action="store_true",
        help="Explicitly print source text for this operator session",
    )
    args = parser.parse_args()

    try:
        sessions = get_session_factory()

        with sessions() as session:
            context = resolve_authorized_scope(session, channel=args.channel, group_id=args.group)

        service = HybridRetrievalService(sessions, EmbeddingService())
        response = service.search(args.query, context, args.top_k)

    except ScopeAuthorizationError:
        parser.exit(1, "No enabled mock binding for this operator scope; verify the seeded group\n")
    except ValueError:
        parser.exit(
            1,
            "Invalid query: use nonblank text of at most 1000 characters within the E5 token limit\n",
        )
    except Exception as error:
        parser.exit(
            1,
            f"{type(error).__name__}: hybrid search failed; check the cached model and migrated database\n",
        )

    excluded = None if args.include_content else {"hits": {"__all__": {"content"}}}
    print(
        json.dumps(response.model_dump(mode="json", exclude=excluded), ensure_ascii=False, indent=2)
    )


if __name__ == "__main__":
    main()
