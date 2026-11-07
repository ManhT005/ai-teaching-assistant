"""Opt-in real Gemini smoke; deterministic embeddings and supplied sample sources."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_generation_events import add_event
from test_hybrid_service import QueryEmbedding

from app.config import Settings
from app.events.processor import EventProcessor
from app.generation.evidence import EvidenceGate
from app.generation.pipeline import AnswerPipeline
from app.knowledge.ingestion import deduplicate_sources
from app.knowledge.store import KnowledgeStore, group_documents
from app.llm.provider import GeminiLLM
from app.retrieval.service import HybridRetrievalService
from scripts.check_sample_materials import load_sample_chunks


def test_live_gemini_grounded_and_no_evidence_cases(authorized_contexts):
    if os.getenv("RUN_LIVE_GENERATION") != "1":
        pytest.skip("Set RUN_LIVE_GENERATION=1 to make at most two configured Gemini API requests")

    settings = Settings()
    if not settings.gemini_api_key or not settings.gemini_model:
        pytest.skip("BLOCKED_EXTERNAL_CREDENTIAL: configure GEMINI_API_KEY and GEMINI_MODEL")

    sessions, contexts = authorized_contexts
    embeddings = QueryEmbedding()
    store = KnowledgeStore(sessions, embeddings)
    for sections in group_documents(deduplicate_sources(load_sample_chunks())):
        store.ingest(sections)

    class CountingGemini(GeminiLLM):
        calls = 0

        def generate(self, question, evidence):
            self.calls += 1
            return super().generate(question, evidence)

    provider = CountingGemini(settings)
    processor = EventProcessor(
        sessions,
        AnswerPipeline(
            HybridRetrievalService(sessions, embeddings),
            EvidenceGate(sessions),
            provider,
        ),
    )
    report = {
        "mode": "live_gemini_with_deterministic_embeddings",
        "source": "supplied simulated PDF/DOCX/XLSX samples; pending comparison excluded",
        "model": settings.gemini_model,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "cases": [],
    }
    for case_id, code, question, expected in [
        ("live-401", "AUTO-N8N-01", "401 Unauthorized", "ANSWERED"),
        ("live-sumifs", "GS-KPI-01", "SUMIFS", "ANSWERED"),
        ("live-comparison", "AUTO-N8N-01", "RAG khác Fine-tuning?", "HANDOFF"),
        ("live-policy", "AUTO-N8N-01", "Giảm học phí?", "HANDOFF"),
    ]:
        result = processor.process_event(add_event(sessions, contexts[code], question))
        report["cases"].append(
            {
                "id": case_id,
                "status": result.status,
                "expected": expected,
                "reason_code": result.reason_code,
                "citation_sections": sorted({citation.section_id for citation in result.citations}),
                "usage": result.model_usage.model_dump() if result.model_usage else None,
            }
        )
    report["provider_requests"] = provider.calls
    Path(".pytest_cache/generation-live-smoke.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    assert provider.calls == 2
    assert all(case["status"] == case["expected"] for case in report["cases"])
