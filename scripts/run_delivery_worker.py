"""Run the local mock outbox dispatcher as a separate worker process."""

import argparse
import signal
from threading import Event

from app.config import get_settings
from app.db import get_session_factory
from app.delivery.dispatcher import DeliveryWorker
from app.delivery.mock_adapter import MockReplyAdapter


def build_worker():
    settings = get_settings()
    sessions = get_session_factory()
    return DeliveryWorker(
        sessions,
        MockReplyAdapter(sessions),
        lease_seconds=settings.delivery_lease_seconds,
        max_attempts=settings.delivery_max_attempts,
    )


def main(argv=None, *, worker=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Dispatch one batch and exit")
    parser.add_argument("--limit", type=int, choices=range(1, 101), default=10)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--worker-id", default="delivery-worker")
    args = parser.parse_args(argv)
    if not 0 < args.poll_interval <= 60:
        parser.error("--poll-interval must be greater than 0 and at most 60 seconds")
    if not args.worker_id or len(args.worker_id) > 120:
        parser.error("--worker-id must contain 1 to 120 characters")

    worker = worker or build_worker()
    if args.once:
        return worker.run_once(limit=args.limit, worker_id=args.worker_id)

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
