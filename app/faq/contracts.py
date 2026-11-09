"""FAQ proposals carry content and versions, never trusted actors or scope."""

from typing import Literal

from pydantic import Field, field_validator

from app.teacher.contracts import Command, clean_text


class FAQContent(Command):
    question: str = Field(min_length=1, max_length=700)
    answer: str = Field(min_length=20, max_length=4000)
    topics: list[str] = Field(default_factory=list, max_length=10)
    source_references: list[str] = Field(default_factory=list, max_length=10)
    _text = field_validator("question", "answer")(clean_text)

    @field_validator("topics", "source_references")
    @classmethod
    def bounded_labels(cls, values):
        if any(len(value) > 100 for value in values):
            raise ValueError("Metadata label too long")
        return [clean_text(value) for value in values]


class FAQCreate(FAQContent):
    ticket_id: int = Field(ge=1)


class FAQReview(Command):
    decision: Literal["approve", "reject"]
    candidate_body_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(min_length=1, max_length=500)
    _text = field_validator("reason")(clean_text)


class FAQRevoke(Command):
    reason: str = Field(min_length=1, max_length=500)
    _text = field_validator("reason")(clean_text)
