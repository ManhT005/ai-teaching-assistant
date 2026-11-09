"""Run only the separate local mock teacher-reply worker."""

import argparse
from threading import Event

from app.config import get_settings
from app.db import get_session_factory
from app.teacher.delivery import TeacherReplyWorker


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--poll-interval", type=float, default=1)
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 100 or not 0 < args.poll_interval <= 60:
        parser.error("Use limit 1..100 and poll interval (0,60]")
    settings = get_settings()
    worker = TeacherReplyWorker(
        get_session_factory(),
        lease_seconds=settings.teacher_reply_lease_seconds,
        max_attempts=settings.teacher_reply_max_attempts,
    )
    stop = Event()
    try:
        while True:
            worker.run_once(limit=args.limit)
            if args.once:
                return
            stop.wait(args.poll_interval)
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
