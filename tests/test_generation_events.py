from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from generation_samples import TEXT
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.db import get_db
from app.events.processor import EventProcessor
from app.generation.contracts import ProviderResponse, Usage
from app.llm.provider import FakeLLM, ProviderFailure
from app.main import app
from app.models import Answer, ChannelBinding, Event, ModelUsage, Ticket
from scripts.process_pending_events import main


def add_event(sessions, scope, question="n8n 401 Unauthorized"):
    with sessions.begin() as session:
        event = Event(
            channel=scope.channel,
            group_id=scope.group_id,
            class_id=scope.class_id,
            external_event_id=uuid4().hex,
            sender_id="test-student",
            body="/hoi " + question,
        )
        session.add(event)
        session.flush()
        return event.id


class CountingProvider(FakeLLM):
    def __init__(self, engine):
        self.calls = 0
        self.engine = engine
        self.lock = Lock()

    def generate(self, question, evidence):
        assert self.engine.pool.checkedout() == 0
        with self.lock:
            self.calls += 1
        return super().generate(question, evidence)


def test_answer_ticket_usage_replay_and_cli(generation_system, capsys):
    sessions, contexts, _, pipeline = generation_system
    scope = contexts["AUTO-N8N-01"]
    provider = CountingProvider(sessions.kw["bind"])
    pipeline.provider = provider
    processor = EventProcessor(sessions, pipeline)
    event_id = add_event(sessions, scope)
    answer = processor.process_event(event_id)
    assert answer.status == "ANSWERED"
    assert answer.reason_code == "GROUNDED_ANSWER"
    assert TEXT in answer.reply_text and "[1]" in answer.reply_text
    assert answer.citations[0].document_version == 1
    assert answer.model_usage.prompt_tokens is None
    assert processor.process_event(event_id) == answer
    assert provider.calls == 1

    handoff_id = add_event(sessions, scope, "RAG khác Fine-tuning?")
    handoff = processor.process_event(handoff_id)
    assert handoff.status == "HANDOFF" and handoff.ticket_id
    assert handoff.reason_code == "INCOMPLETE_EVIDENCE"
    assert not handoff.citations and handoff.model_usage is None
    assert processor.process_event(handoff_id) == handoff
    assert provider.calls == 1

    with sessions() as session:
        event = session.get(Event, event_id)
        assert event.attempts == 1 and event.processing_started_at and event.finished_at
        assert session.scalar(select(func.count()).select_from(Answer)) == 1
        assert session.scalar(select(func.count()).select_from(Ticket)) == 1
        assert session.scalar(select(func.count()).select_from(ModelUsage)) == 1
        ticket = session.get(Ticket, handoff.ticket_id)
        assert ticket.status == "OPEN" and ticket.class_id == scope.class_id
        assert ticket.evidence_summary["reason_code"] == handoff.reason_code

    main(["--once", "--event-id", str(event_id)], sessions=sessions, processor=processor)
    output = capsys.readouterr().out
    assert "ANSWERED" in output and TEXT not in output
    main(
        ["--once", "--event-id", str(event_id), "--preview"], sessions=sessions, processor=processor
    )
    assert TEXT in capsys.readouterr().out
    assert provider.calls == 1


def test_invalid_binding_or_event_blocks_retrieval_before_provider(generation_system, monkeypatch):
    sessions, contexts, _, pipeline = generation_system
    scope = contexts["AUTO-N8N-01"]
    event_id = add_event(sessions, scope)
    monkeypatch.setattr(pipeline, "run", lambda *_: pytest.fail("Revoked scope reached pipeline"))
    with sessions.begin() as session:
        session.get(ChannelBinding, scope.binding_id).active = False

    result = EventProcessor(sessions, pipeline).process_event(event_id)
    assert result.status == "FAILED_FINAL" and result.reason_code == "SCOPE_REVOKED"
    assert not result.citations and result.ticket_id is None


def test_provider_failure_and_invalid_drafts_do_not_publish(generation_system, caplog):
    sessions, contexts, _, pipeline = generation_system
    scope = contexts["AUTO-N8N-01"]

    class FailedProvider:
        def __init__(self, retryable):
            self.retryable = retryable

        def generate(self, *_):
            raise ProviderFailure(retryable=self.retryable)

    processor = EventProcessor(sessions, pipeline)
    for retryable in (True, False):
        pipeline.provider = FailedProvider(retryable)
        result = processor.process_event(add_event(sessions, scope))
        assert result.status == ("FAILED_RETRYABLE" if retryable else "FAILED_FINAL")
        assert result.reason_code == "PROVIDER_FAILURE"
        assert result.model_usage is None and result.ticket_id is None

    class BadDraft:
        def __init__(self, text):
            self.text = text

        def generate(self, *_):
            return ProviderResponse(
                text=self.text,
                usage=Usage(
                    provider="fake",
                    model="adversarial",
                    prompt_tokens=10,
                    completion_tokens=4,
                ),
            )

    for text, reason in [
        ("not json", "UNSUPPORTED_CLAIM"),
        (
            '{"answer_text":"x","claims":[{"text":"x","citation_ids":["fake"]}],"needs_handoff":false}',
            "INVALID_CITATION",
        ),
    ]:
        pipeline.provider = BadDraft(text)
        result = processor.process_event(add_event(sessions, scope))
        assert result.status == "HANDOFF" and result.reason_code == reason
        assert result.model_usage.prompt_tokens == 10

    assert "test-student" not in caplog.text and TEXT not in caplog.text
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Answer)) == 0
        assert session.scalar(select(func.count()).select_from(ModelUsage)) == 2


def test_revoke_during_generation_prevents_answer_but_preserves_usage(generation_system):
    sessions, contexts, store, pipeline = generation_system

    class RevokingProvider(FakeLLM):
        def generate(self, question, evidence):
            assert sessions.kw["bind"].pool.checkedout() == 0
            store.revoke("AUTO", "withdrawn while provider is running")
            return super().generate(question, evidence)

    pipeline.provider = RevokingProvider()
    result = EventProcessor(sessions, pipeline).process_event(
        add_event(sessions, contexts["AUTO-N8N-01"])
    )
    assert result.status == "FAILED_FINAL" and result.reason_code == "SCOPE_REVOKED"
    assert result.reply_text is None and not result.citations
    assert result.model_usage.provider == "fake"
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Answer)) == 0
        assert session.scalar(select(func.count()).select_from(Ticket)) == 0


def test_binding_change_during_generation_blocks_finalization(generation_system):
    sessions, contexts, _, pipeline = generation_system
    scope = contexts["AUTO-N8N-01"]

    class BindingChangingProvider(FakeLLM):
        def generate(self, question, evidence):
            with sessions.begin() as session:
                session.get(ChannelBinding, scope.binding_id).class_id = contexts[
                    "GS-KPI-01"
                ].class_id
            return super().generate(question, evidence)

    pipeline.provider = BindingChangingProvider()
    result = EventProcessor(sessions, pipeline).process_event(add_event(sessions, scope))
    assert result.status == "FAILED_FINAL" and result.reason_code == "SCOPE_REVOKED"
    assert result.model_usage is not None and not result.citations


def test_invalid_question_and_class_mismatch_fail_without_pipeline(generation_system, monkeypatch):
    sessions, contexts, _, pipeline = generation_system
    scope = contexts["AUTO-N8N-01"]
    processor = EventProcessor(sessions, pipeline)
    monkeypatch.setattr(pipeline, "run", lambda *_: pytest.fail("Invalid event reached pipeline"))
    for body in ("ordinary chat", "/hoi", "/hoi " + "x" * 1001):
        event_id = add_event(sessions, scope)
        with sessions.begin() as session:
            session.get(Event, event_id).body = body
        assert processor.process_event(event_id).reason_code == "INVALID_EVENT"

    event_id = add_event(sessions, scope)
    with sessions.begin() as session:
        session.get(Event, event_id).class_id = contexts["GS-KPI-01"].class_id
    assert processor.process_event(event_id).reason_code == "SCOPE_REVOKED"


def test_atomic_claim_has_single_provider_call_and_terminal_outcome(generation_system):
    sessions, contexts, _, pipeline = generation_system

    class ConcurrentProvider(FakeLLM):
        calls = 0
        lock = Lock()

        def generate(self, question, evidence):
            with self.lock:
                self.calls += 1
            return super().generate(question, evidence)

    provider = ConcurrentProvider()
    pipeline.provider = provider
    processor = EventProcessor(sessions, pipeline)
    event_id = add_event(sessions, contexts["AUTO-N8N-01"])
    barrier = Barrier(2)

    def process():
        barrier.wait(timeout=10)
        return processor.process_event(event_id)

    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(lambda _: process(), range(2)))

    assert provider.calls == 1
    assert "ANSWERED" in {outcome.status for outcome in outcomes}
    assert processor.process_event(event_id).status == "ANSWERED"
    with sessions() as session:
        assert session.get(Event, event_id).attempts == 1
        assert session.scalar(select(func.count()).select_from(Answer)) == 1
        assert session.scalar(select(func.count()).select_from(Ticket)) == 0


def test_database_guard_serializes_independent_answer_ticket_writers(generation_system):
    sessions, contexts, _, _ = generation_system
    scope = contexts["AUTO-N8N-01"]
    event_id = add_event(sessions, scope)
    barrier = Barrier(2)

    def write(kind):
        try:
            with sessions.begin() as session:
                barrier.wait(timeout=10)
                if kind == "answer":
                    session.add(
                        Answer(
                            event_id=event_id,
                            decision="ANSWER",
                            reply_text="test",
                            source_chunks=[],
                        )
                    )
                else:
                    session.add(Ticket(event_id=event_id, class_id=scope.class_id, reason="test"))
            return "saved"
        except IntegrityError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(write, ["answer", "ticket"]))
    assert sorted(outcomes) == ["rejected", "saved"]


def test_duplicate_webhook_to_cli_persists_one_decision(generation_system, monkeypatch, capsys):
    sessions, contexts, _, pipeline = generation_system
    scope = contexts["AUTO-N8N-01"]
    monkeypatch.setenv("MOCK_WEBHOOK_TOKEN", "test-only")
    get_settings.cache_clear()

    def db_dependency():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = db_dependency
    payload = {
        "event_id": uuid4().hex,
        "group_id": scope.group_id,
        "sender_id": "test-student",
        "text": "/hoi n8n 401 Unauthorized",
        "received_at": "2026-11-07T14:00:00+07:00",
    }
    try:
        with TestClient(app) as client:
            headers = {"X-Webhook-Token": "test-only"}
            assert (
                client.post(
                    "/webhook/mock", json={**payload, "class_id": "GS-KPI-01"}, headers=headers
                ).status_code
                == 422
            )
            assert client.post("/webhook/mock", json=payload, headers=headers).status_code == 202
            assert (
                client.post("/webhook/mock", json=payload, headers=headers).json()["status"]
                == "duplicate"
            )
        main(
            ["--once", "--limit", "5", "--preview"],
            sessions=sessions,
            processor=EventProcessor(sessions, pipeline),
        )
        assert "ANSWERED" in capsys.readouterr().out
        with sessions() as session:
            assert session.scalar(select(func.count()).select_from(Event)) == 1
            assert session.scalar(select(func.count()).select_from(Answer)) == 1
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
