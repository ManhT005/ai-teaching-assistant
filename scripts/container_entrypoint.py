"""Configure container networking and run a one-shot bootstrap or an application process."""

import logging
import os
import sys
from pathlib import Path

from app.db_url import configure_database


def validate_runtime():
    from app.config import get_settings
    from app.llm.provider import build_provider

    settings = get_settings()
    secrets = []
    for name in ("mock_webhook_token", "operator_api_token"):
        secret = getattr(settings, name)
        value = secret.get_secret_value() if secret else ""
        if len(value) < 32:
            raise ValueError(f"Set {name.upper()} to at least 32 random characters")
        secrets.append(value)
    if secrets[0] == secrets[1]:
        raise ValueError("MOCK_WEBHOOK_TOKEN and OPERATOR_API_TOKEN must be different")
    build_provider(settings)
    return settings


def bootstrap(settings):
    from alembic import command
    from alembic.config import Config

    from app.db import get_session_factory
    from app.knowledge.embeddings import EmbeddingService
    from app.knowledge.source_manifest import load_manifest
    from app.knowledge.store import KnowledgeStore
    from scripts.seed import seed

    # Verify all inputs before changing the database.
    manifest = Path(os.environ["KNOWLEDGE_MANIFEST"])
    sections = load_manifest(manifest)
    embeddings = EmbeddingService(settings)
    embeddings.encode_query("Kiểm tra model E5 khi khởi động")
    command.upgrade(Config("alembic.ini"), "head")
    seed()
    store = KnowledgeStore(get_session_factory(), embeddings, settings)
    bootstrap_knowledge(store, sections)
    print(
        "Bootstrap completed: migrations, class bindings, E5 and knowledge are ready.", flush=True
    )


def bootstrap_knowledge(store, sections):
    from sqlalchemy import or_, select

    from app.knowledge.store import group_documents
    from app.models import Document

    for group in group_documents(sections):
        logical_key = group[0].doc_id
        with store.sessions() as session:
            preserved = session.scalar(
                select(Document.id)
                .where(
                    Document.logical_key == logical_key,
                    or_(
                        (Document.active.is_(True)) & (Document.index_status == "READY"),
                        Document.index_status == "REVOKED",
                    ),
                )
                .limit(1)
            )
        if preserved:
            # Keep manual source/review changes. Failed initial imports remain retryable.
            print(f"Knowledge {logical_key}: preserving existing ready/revoked history")
            continue
        result = store.ingest(group)
        print(f"Knowledge {result['doc_id']}: {result['status']}", flush=True)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        raise SystemExit("Provide bootstrap or an application command")
    try:
        configure_database(os.environ)
        settings = validate_runtime()
        logging.basicConfig(level=settings.log_level, format="%(levelname)s %(message)s")
        if args == ["bootstrap"]:
            bootstrap(settings)
            return
    except Exception as error:  # noqa: BLE001 -- never leak DB/provider secrets at startup
        # DB/provider/parser exceptions can contain credentials or source passages.
        print(
            f"Container startup failed ({type(error).__name__}). Check POSTGRES_*, distinct "
            "32-character tokens, provider settings, KNOWLEDGE_MANIFEST and the bundled E5 model.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    os.execvp(args[0], args)


if __name__ == "__main__":
    main()
