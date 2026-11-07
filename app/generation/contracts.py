"""Strict internal contracts for bounded grounded answers; no public ask API."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ReasonCode(StrEnum):
    NO_AUTHORIZED_SOURCE = "NO_AUTHORIZED_SOURCE"
    INCOMPLETE_EVIDENCE = "INCOMPLETE_EVIDENCE"
    AMBIGUOUS_QUESTION = "AMBIGUOUS_QUESTION"
    SENSITIVE_OR_POLICY = "SENSITIVE_OR_POLICY"
    UNVERIFIED_RELEVANCE = "UNVERIFIED_RELEVANCE"
    INVALID_CITATION = "INVALID_CITATION"
    UNSUPPORTED_CLAIM = "UNSUPPORTED_CLAIM"
    UNSAFE_RESPONSE = "UNSAFE_RESPONSE"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    SCOPE_REVOKED = "SCOPE_REVOKED"
    GROUNDED_ANSWER = "GROUNDED_ANSWER"
    INVALID_EVENT = "INVALID_EVENT"
    PROCESSING_FAILURE = "PROCESSING_FAILURE"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class EvidenceQuote(StrictModel):
    quote_id: str = Field(pattern=r"^q[0-9]{3}$")
    chunk_id: str = Field(min_length=1)
    text: str = Field(min_length=20, max_length=2000)


class AnswerSelection(StrictModel):
    needs_handoff: bool
    quote_ids: list[str] = Field(min_length=0, max_length=3)


class Claim(StrictModel):
    text: str = Field(min_length=1, max_length=2000)
    citation_ids: list[str] = Field(min_length=1, max_length=3)


class AnswerDraft(StrictModel):
    answer_text: str = Field(min_length=1, max_length=6000)
    claims: list[Claim] = Field(min_length=1, max_length=8)
    needs_handoff: bool


class Usage(StrictModel):
    provider: str
    model: str
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    request_id: str | None = Field(default=None, max_length=255)


class ProviderResponse(StrictModel):
    text: str = Field(max_length=24000)
    usage: Usage


class Citation(StrictModel):
    chunk_id: str
    doc_id: str
    document_version: int
    section_id: str
    source_name: str
    locator: str
    class_scope: str


class EventProcessingResult(StrictModel):
    event_id: int
    status: str
    decision: str | None = None
    reason_code: str | None = None
    reply_text: str | None = None
    citations: list[Citation] = Field(default_factory=list)
    ticket_id: int | None = None
    model_usage: Usage | None = None
