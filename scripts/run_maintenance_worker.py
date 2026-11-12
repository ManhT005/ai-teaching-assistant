"""Periodically prune expired webhook quota windows without an external scheduler."""

import os
import signal
from threading import Event

from scripts.cleanup_rate_limits import main as cleanup


def main():
    interval = int(os.environ.get("MAINTENANCE_INTERVAL_SECONDS", "3600"))
    if not 60 <= interval <= 86400:
        raise SystemExit("MAINTENANCE_INTERVAL_SECONDS must be between 60 and 86400")
    stop = Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    while not stop.is_set():
        cleanup()
        stop.wait(interval)


if __name__ == "__main__":
    main()
