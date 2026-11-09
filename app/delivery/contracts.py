"""Small provider-neutral contract for outbound mock messages."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class DeliveryReceipt:
    external_message_id: str
    duplicate: bool


@dataclass(frozen=True)
class DeliveryMessage:
    message_key: str
    outbox_id: int
    lease_token: str
    channel: str
    group_id: str
    class_id: int
    kind: str
    body: str
    citations: list[dict]


class DeliveryAdapter(Protocol):
    def send(self, message: DeliveryMessage) -> DeliveryReceipt: ...


class DeliveryFailure(Exception):
    def __init__(self, *, retryable: bool):
        super().__init__("Mock delivery failed")
        self.retryable = retryable


class DeliverySuppressed(Exception):
    """Current binding or cited source is no longer eligible for delivery."""


class DeliveryLeaseLost(Exception):
    """The delivery claim expired or was replaced before the mock side effect."""
