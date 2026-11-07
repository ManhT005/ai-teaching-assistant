"""Operator-only single pass over stored events; output is not outbound delivery."""

import argparse
import json

from sqlalchemy import select

from app.config import get_settings
from app.db import get_session_factory
from app.embeddings import EmbeddingService
from app.events.processor import EventProcessor
from app.generation.evidence import EvidenceGate
from app.generation.pipeline import AnswerPipeline
from app.llm.provider import build_provider
from app.models import Event
from app.retrieval import HybridRetrievalService


def main(argv=None, *, sessions=None, processor=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", required=True)
    parser.add_argument("--limit", type=int, choices=range(1, 101), default=5)
    parser.add_argument(
        "--event-id", type=int, help="Internal stored event ID, including terminal replay"
    )
    parser.add_argument(
        "--preview", action="store_true", help="Print reply/citations to the operator"
    )
    args = parser.parse_args(argv)

    try:
        sessions = sessions or get_session_factory()
        with sessions() as session:
            ids = (
                [args.event_id]
                if args.event_id is not None
                else list(
                    session.scalars(
                        select(Event.id)
                        .where(Event.status == "PENDING")
                        .order_by(Event.id)
                        .limit(args.limit)
                    )
                )
            )

        if ids and processor is None:
            settings = get_settings()
            pipeline = AnswerPipeline(
                HybridRetrievalService(sessions, EmbeddingService()),
                EvidenceGate(sessions, max_context_characters=settings.llm_max_context_characters),
                build_provider(settings),
            )
            processor = EventProcessor(sessions, pipeline)

        failed = False
        for event_id in ids:
            result = processor.process_event(event_id)
            excluded = None if args.preview else {"reply_text", "citations"}
            print(json.dumps(result.model_dump(mode="json", exclude=excluded), ensure_ascii=False))
            failed = failed or result.status.startswith("FAILED")

    except Exception:  # noqa: BLE001 -- CLI errors must not expose DB credentials or prompts
        parser.exit(
            1, "Processing failed; verify configuration, stored event and migrated database.\n"
        )

    if failed:
        parser.exit(1, "One or more events failed; inspect reason_code above.\n")


if __name__ == "__main__":
    main()
