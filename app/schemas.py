"""Public API contracts (not database models)."""

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class MockMessageEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=160)
    channel: str = Field(default="mock", pattern="^mock$")
    group_id: str = Field(min_length=1, max_length=160)
    sender_id: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=5000)
    received_at: AwareDatetime
    is_bot: bool = False

    @field_validator("event_id", "group_id", "sender_id")
    @classmethod
    def nonblank_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Identity must not be blank")
        return value


class WebhookAck(BaseModel):
    status: str
    event_id: str
    detail: str | None = None
