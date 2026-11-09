"""Isolated synthetic staff/FAQ fixtures; never real human curriculum approval."""

from test_generation_events import add_event

from app.api.faq import faq_publisher
from app.db import get_db
from app.events.processor import EventProcessor
from app.faq.publisher import FAQPublisher
from app.main import app
from app.teacher.auth import authenticate, issue_credential
from app.workflow_models import StaffActor, StaffClassGrant


def build_staff_system(generation_system, tmp_path):
    sessions, scopes, store, pipeline = generation_system
    tokens, identities = {}, {}
    with sessions.begin() as session:
        for alias, class_code, roles in [
            ("fixture-author", "AUTO-N8N-01", ("TEACHER", "FAQ_REVIEWER", "OPERATOR")),
            ("fixture-reviewer", "AUTO-N8N-01", ("FAQ_REVIEWER", "OPERATOR")),
            ("fixture-other-teacher", "AUTO-N8N-01", ("TEACHER",)),
            ("fixture-gs", "GS-KPI-01", ("TEACHER", "FAQ_REVIEWER", "OPERATOR")),
        ]:
            actor = StaffActor(alias=alias, active=True)
            session.add(actor)
            session.flush()
            for role in roles:
                session.add(
                    StaffClassGrant(
                        actor_id=actor.id, class_id=scopes[class_code].class_id, role=role
                    )
                )
            tokens[alias] = issue_credential(session, actor.id)
    with sessions() as session:
        identities = {alias: authenticate(session, token) for alias, token in tokens.items()}
    tickets = {}
    for code, scope in scopes.items():
        event_id = add_event(sessions, scope, "RAG vs Fine-tuning")
        result = EventProcessor(sessions, pipeline).process_event(event_id)
        assert result.status == "HANDOFF"
        tickets[code] = result.ticket_id

    def database():
        with sessions() as session:
            yield session

    publisher = FAQPublisher(sessions, store, tmp_path / "faq")
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[faq_publisher] = lambda: publisher
    return {
        "sessions": sessions,
        "scopes": scopes,
        "store": store,
        "pipeline": pipeline,
        "tokens": tokens,
        "identities": identities,
        "tickets": tickets,
        "publisher": publisher,
    }


def headers(system, alias="fixture-author"):
    return {"X-Teacher-Token": system["tokens"][alias]}


def queue_reply(client, system):
    ticket_id = system["tickets"]["AUTO-N8N-01"]
    path = f"/internal/teacher/tickets/{ticket_id}"
    result = client.post(
        path + "/claim",
        headers=headers(system),
        json={"expected_version": 1, "idempotency_key": "claim-test-0001"},
    )
    assert result.status_code == 200, result.text
    result = client.post(
        path + "/reply-draft",
        headers=headers(system),
        json={
            "expected_version": 2,
            "idempotency_key": "draft-test-0001",
            "body": "HTTP Request in n8n sends the configured request to the target API.",
        },
    )
    assert result.status_code == 200, result.text
    reply = result.json()["reply"]
    result = client.post(
        path + "/approve-reply",
        headers=headers(system),
        json={
            "expected_version": 3,
            "idempotency_key": "approve-test-0001",
            "reply_revision": reply["revision"],
            "approved_body_hash": reply["body_hash"],
        },
    )
    assert result.status_code == 200, result.text
    return ticket_id, reply["reply_id"]


def resolved_candidate(client, system):
    from app.teacher.delivery import TeacherReplyWorker

    ticket_id, _ = queue_reply(client, system)
    assert TeacherReplyWorker(system["sessions"]).run_once() == ["SENT"]
    result = client.post(
        "/internal/faq/candidates",
        headers=headers(system),
        json={
            "expected_version": 5,
            "idempotency_key": "faq-create-0001",
            "ticket_id": ticket_id,
            "question": "n8n HTTP Request",
            "answer": "n8n HTTP Request sends a configured request to the target API.",
            "topics": ["n8n", "HTTP"],
        },
    )
    assert result.status_code == 200, result.text
    return result.json()


def reviewed_candidate(client, system):
    candidate = resolved_candidate(client, system)
    path = f"/internal/faq/candidates/{candidate['faq_id']}"
    result = client.post(
        path + "/submit",
        headers=headers(system),
        json={"expected_version": 1, "idempotency_key": "faq-submit-0001"},
    )
    assert result.status_code == 200, result.text
    result = client.post(
        path + "/review",
        headers=headers(system, "fixture-reviewer"),
        json={
            "expected_version": 1,
            "idempotency_key": "faq-review-0001",
            "decision": "approve",
            "candidate_body_hash": candidate["body_hash"],
            "reason": "Synthetic automated test review only; not human or GOS approval",
        },
    )
    assert result.status_code == 200, result.text
    return result.json()
