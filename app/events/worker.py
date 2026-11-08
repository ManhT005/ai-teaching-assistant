"""Poll and execute claimed event work without retaining queue transactions."""

from threading import Event as StopEvent


class EventWorker:
    def __init__(self, queue, processor):
        self.queue = queue
        self.processor = processor

    def run_once(self, *, limit: int = 10, worker_id: str = "event-worker"):
        claims = self.queue.claim_due(limit=limit, worker_id=worker_id)
        return [self.processor.process_claim(claim) for claim in claims]

    def run_forever(
        self,
        *,
        poll_interval: float = 1.0,
        limit: int = 10,
        worker_id: str = "event-worker",
        stop: StopEvent | None = None,
    ):
        stop = stop or StopEvent()
        while not stop.is_set():
            self.run_once(limit=limit, worker_id=worker_id)
            stop.wait(poll_interval)
