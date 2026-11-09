"""Bounded teacher commands; actor and destination are always server-derived."""

import hashlib
import json
import re
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.generation.safety import is_sensitive_policy, is_unsafe


class WorkflowError(Exception):
    def __init__(self, code: str, status: int = 409):
        self.code = code
        self.status = status
        super().__init__(code)


def digest(value) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def clean_text(value: str) -> str:
    value = unicodedata.normalize("NFC", value).strip()
    if not value or any(
        unicodedata.category(char).startswith("C") and char not in "\n\t" for char in value
    ):
        raise ValueError("Text is blank or contains control characters")
    if re.search(r"[<>]|\b\S+@\S+\.\S+\b|(?:\+?\d[\s.-]?){9,}", value):
        raise ValueError("Markup and personal contact details are not allowed")
    if is_unsafe(value) or is_sensitive_policy(value):
        raise ValueError("Secret, instruction injection or restricted policy content")
    if value.lstrip().startswith(("=", "+", "-", "@")):
        raise ValueError("Spreadsheet formula prefixes are not allowed")
    return value


def preview(value: str) -> str:
    if is_unsafe(value):
        return "[redacted sensitive question]"
    value = re.sub(r"\b\S+@\S+\.\S+\b|(?:\+?\d[\s.-]?){9,}", "[redacted]", value)
    return " ".join(value.split())[:350]


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9:_-]+$")


class ReplyDraft(Command):
    body: str = Field(min_length=1, max_length=4000)
    _text = field_validator("body")(clean_text)


class ReplyApproval(Command):
    reply_revision: int = Field(ge=1)
    approved_body_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class CloseTicket(Command):
    reason: str = Field(min_length=1, max_length=500)
    _text = field_validator("reason")(clean_text)
