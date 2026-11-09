"""Poll and execute claimed event work without retaining queue transactions."""

from threading import Event as StopEvent


class EventWorker:
    def __init__(self, queue, processor):
        self.queue = queue
        self.processor = processor

    def run_once(self, *, limit: int = 10, worker_id: str = "event-worker"):
        if not 1 <= limit <= 100:
            raise ValueError("Work limit must be between 1 and 100")
        results = []
        for _ in range(limit):
            # Claim just before execution: slow LLM calls must not exhaust the
            # leases of other events waiting in the same polling pass.
            claims = self.queue.claim_due(limit=1, worker_id=worker_id)
            if not claims:
                break
            results.append(self.processor.process_claim(claims[0]))
        return results

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
