"""Public API contracts (not database models)."""
from pydantic import BaseModel, Field


class MockMessageEvent(BaseModel):
    event_id: str = Field(min_length=1, max_length=160)
    channel: str = Field(default="mock", pattern="^mock$")
    group_id: str = Field(min_length=1, max_length=160)
    sender_id: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=5000)
    is_bot: bool = False


class WebhookAck(BaseModel):
    status: str
    event_id: str
    detail: str | None = None
