"""Offline fake and bounded Gemini REST adapter with sanitized failures."""

import json
from typing import Protocol

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.generation.contracts import AnswerSelection, ProviderResponse, Usage
from app.generation.evidence_catalog import CatalogError, catalog_from_units
from app.llm.prompts import SYSTEM_PROMPT, user_payload


class ProviderFailure(Exception):
    def __init__(self, *, retryable: bool):
        super().__init__("Generation provider failed")
        self.retryable = retryable


class LLMProvider(Protocol):
    def generate(self, question: str, evidence: dict[str, tuple[str, ...]]) -> ProviderResponse: ...


class FakeLLM:
    """Deterministic selection of approved quote units, with truthful unknown usage."""

    def __init__(self, *, max_quotes: int = 1):
        if type(max_quotes) is not int or not 1 <= max_quotes <= 3:
            raise ValueError("Fake selection accepts one to three quotes")
        self.max_quotes = max_quotes

    def generate(self, question: str, evidence: dict[str, tuple[str, ...]]) -> ProviderResponse:
        catalog = catalog_from_units(evidence)
        selection = AnswerSelection(
            needs_handoff=not catalog.quotes,
            quote_ids=[quote.quote_id for quote in catalog.quotes[: self.max_quotes]],
        )
        return ProviderResponse(
            text=selection.model_dump_json(), usage=Usage(provider="fake", model="selection-v1")
        )


class GeminiLLM:
    def __init__(self, settings: Settings, *, transport=None):
        if not settings.gemini_api_key or not settings.gemini_api_key.get_secret_value().strip():
            raise ValueError("Configure GEMINI_API_KEY for the Gemini provider")
        if not settings.gemini_model:
            raise ValueError("Configure GEMINI_MODEL for the Gemini provider")

        self.settings = settings
        self.transport = transport

    def generate(self, question: str, evidence: dict[str, tuple[str, ...]]) -> ProviderResponse:
        settings = self.settings
        try:
            catalog = catalog_from_units(evidence)
        except CatalogError:
            raise ProviderFailure(retryable=False) from None
        if not catalog.quotes:
            raise ProviderFailure(retryable=False)
        schema = AnswerSelection.model_json_schema()
        schema["properties"]["quote_ids"]["items"]["enum"] = [
            quote.quote_id for quote in catalog.quotes
        ]
        body = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": user_payload(question, evidence)}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": settings.llm_max_output_tokens,
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
            },
        }

        try:
            # The key is sent only in a header, never a URL.
            with (
                httpx.Client(
                    timeout=settings.llm_timeout_seconds,
                    transport=self.transport,
                    follow_redirects=False,
                    trust_env=False,
                ) as client,
                client.stream(
                    "POST",
                    f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent",
                    headers={"x-goog-api-key": settings.gemini_api_key.get_secret_value()},
                    json=body,
                ) as response,
            ):
                if response.status_code != 200:
                    raise ProviderFailure(
                        retryable=response.status_code in {408, 429} or response.status_code >= 500
                    )

                payload = bytearray()
                for block in response.iter_bytes():
                    payload.extend(block)
                    if len(payload) > 128000:
                        raise ProviderFailure(retryable=False)

            data = json.loads(payload)
            candidate = data["candidates"][0]
            if candidate.get("finishReason") != "STOP":
                raise ProviderFailure(retryable=False)

            text = "".join(
                part["text"]
                for part in candidate["content"]["parts"]
                if "text" in part and not part.get("thought", False)
            )
            metadata = data.get("usageMetadata", {})
            return ProviderResponse(
                text=text,
                usage=Usage(
                    provider="gemini",
                    model=settings.gemini_model,
                    prompt_tokens=metadata.get("promptTokenCount"),
                    completion_tokens=metadata.get("candidatesTokenCount"),
                    request_id=data.get("responseId"),
                ),
            )

        except ProviderFailure:
            raise
        except httpx.TransportError:
            raise ProviderFailure(retryable=True) from None
        except (ValueError, KeyError, IndexError, TypeError, ValidationError):
            raise ProviderFailure(retryable=False) from None


def build_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "fake":
        return FakeLLM()
    return GeminiLLM(settings)
