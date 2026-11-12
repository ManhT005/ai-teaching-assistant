"""Run the PostgreSQL event processor as a separate worker process."""

import argparse
import json
import signal
from threading import Event

from app.config import get_settings
from app.db import get_session_factory
from app.events.processor import EventProcessor
from app.events.worker import EventWorker
from app.generation.evidence import EvidenceGate
from app.generation.pipeline import AnswerPipeline
from app.knowledge.embeddings import EmbeddingService
from app.llm.provider import build_provider
from app.retrieval.service import HybridRetrievalService


def build_worker():
    settings = get_settings()
    sessions = get_session_factory()
    pipeline = AnswerPipeline(
        HybridRetrievalService(sessions, EmbeddingService()),
        EvidenceGate(sessions, max_context_characters=settings.llm_max_context_characters),
        build_provider(settings),
    )
    processor = EventProcessor(sessions, pipeline)
    return EventWorker(processor.queue, processor)


def main(argv=None, *, worker=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Process one batch and exit")
    parser.add_argument("--limit", type=int, choices=range(1, 101), default=10)
    parser.add_argument(
        "--poll-interval", type=float, default=get_settings().event_poll_interval_seconds
    )
    parser.add_argument("--worker-id", default="event-worker")
    args = parser.parse_args(argv)
    if args.poll_interval <= 0 or args.poll_interval > 60:
        parser.error("--poll-interval must be greater than 0 and at most 60 seconds")
    if not args.worker_id or len(args.worker_id) > 120:
        parser.error("--worker-id must contain 1 to 120 characters")

    worker = worker or build_worker()
    if args.once:
        results = worker.run_once(limit=args.limit, worker_id=args.worker_id)
        for result in results:
            print(json.dumps(result.model_dump(mode="json", exclude={"reply_text", "citations"})))
        return results

    stop = Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    worker.run_forever(
        poll_interval=args.poll_interval,
        limit=args.limit,
        worker_id=args.worker_id,
        stop=stop,
    )
    return []


if __name__ == "__main__":
    main()
