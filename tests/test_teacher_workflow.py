from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from teacher_faq_helpers import headers, queue_reply

from app.models import Answer, ChannelBinding, Event, OutboxMessage, Ticket
from app.teacher.contracts import Command, WorkflowError
from app.teacher.delivery import TeacherReplyWorker
from app.teacher.service import transition
from app.workflow_models import (
    StaffCredential,
    TeacherMockReceipt,
    TeacherReplyOutbox,
    TicketAction,
)


def test_class_scoped_queue_and_forged_actor_rejected(staff_system):
    client, system = staff_system
    items = client.get("/internal/teacher/tickets", headers=headers(system)).json()["items"]
    assert [item["ticket_id"] for item in items] == [system["tickets"]["AUTO-N8N-01"]]
    gs = system["tickets"]["GS-KPI-01"]
    assert client.get(f"/internal/teacher/tickets/{gs}", headers=headers(system)).status_code == 404
    assert (
        client.post(
            f"/internal/teacher/tickets/{gs}/claim",
            headers=headers(system),
            json={"expected_version": 1, "idempotency_key": "claim-wrong-class"},
        ).status_code
        == 404
    )
    ticket = system["tickets"]["AUTO-N8N-01"]
    assert (
        client.post(
            f"/internal/teacher/tickets/{ticket}/claim",
            headers=headers(system),
            json={"expected_version": 1, "idempotency_key": "forged-actor-test", "actor_id": 123},
        ).status_code
        == 422
    )


@pytest.mark.parametrize("mode", ["missing", "wrong", "expired", "revoked"])
def test_staff_credential_authentication(staff_system, mode):
    client, system = staff_system
    request_headers = headers(system)
    with system["sessions"].begin() as session:
        credential = session.get(
            StaffCredential, system["identities"]["fixture-author"].credential_id
        )
        assert credential.token_hash != system["tokens"]["fixture-author"]
        if mode == "expired":
            credential.expires_at = session.scalar(select(func.now())) - timedelta(seconds=1)
        elif mode == "revoked":
            credential.revoked_at = session.scalar(select(func.now()))
        elif mode == "missing":
            request_headers = {}
        else:
            request_headers = {"X-Teacher-Token": "wrong"}
    assert client.get("/internal/teacher/tickets", headers=request_headers).status_code == 401


def test_concurrent_claim_has_one_winner(staff_system):
    _, system = staff_system
    ticket_id = system["tickets"]["AUTO-N8N-01"]

    def claim(alias):
        try:
            with system["sessions"].begin() as session:
                transition(
                    session,
                    system["identities"][alias],
                    ticket_id,
                    "claim",
                    Command(expected_version=1, idempotency_key=f"claim-{alias}"),
                )
            return 200
        except WorkflowError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(claim, ["fixture-author", "fixture-other-teacher"])) == [200, 409]
    with system["sessions"]() as session:
        assert session.scalar(select(func.count()).select_from(TicketAction)) == 1


def test_approval_replay_and_separate_receipt_preserve_original_outcome(staff_system):
    client, system = staff_system
    ticket_id, reply_id = queue_reply(client, system)
    detail = client.get(f"/internal/teacher/tickets/{ticket_id}", headers=headers(system)).json()
    reply = detail["reply"]
    command = {
        "expected_version": 3,
        "idempotency_key": "approve-test-0001",
        "reply_revision": reply["revision"],
        "approved_body_hash": reply["body_hash"],
    }
    assert (
        client.post(
            f"/internal/teacher/tickets/{ticket_id}/approve-reply",
            headers=headers(system),
            json=command,
        ).status_code
        == 200
    )
    worker = TeacherReplyWorker(system["sessions"])
    assert worker.run_once() == ["SENT"]
    assert worker.run_once() == []
    with system["sessions"]() as session:
        ticket = session.get(Ticket, ticket_id)
        assert ticket.status == "RESOLVED" and ticket.resolved_at is not None
        assert session.get(Event, ticket.event_id).status == "HANDOFF"
        assert session.scalar(select(Answer).where(Answer.event_id == ticket.event_id)) is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutboxMessage)
                .where(OutboxMessage.event_id == ticket.event_id)
            )
            == 1
        )
        receipt = session.scalar(select(TeacherMockReceipt))
        assert receipt.body.startswith("Phản hồi của giảng viên:")
    assert (
        client.get(
            f"/internal/teacher/replies/{reply_id}/delivery", headers=headers(system)
        ).json()["delivery_status"]
        == "SENT"
    )


@pytest.mark.parametrize("mode", ["binding", "credential", "hash"])
def test_delivery_revalidates_authorization_and_approval(staff_system, mode):
    client, system = staff_system
    ticket_id, _ = queue_reply(client, system)
    with system["sessions"].begin() as session:
        if mode == "binding":
            session.get(ChannelBinding, system["scopes"]["AUTO-N8N-01"].binding_id).active = False
        elif mode == "credential":
            session.get(
                StaffCredential, system["identities"]["fixture-author"].credential_id
            ).revoked_at = session.scalar(select(func.now()))
        else:
            session.scalar(select(TeacherReplyOutbox)).approved_hash = "0" * 64
    assert TeacherReplyWorker(system["sessions"]).run_once() == ["SUPPRESSED"]
    with system["sessions"]() as session:
        assert session.get(Ticket, ticket_id).status == "REPLY_QUEUED"
        assert session.scalar(select(func.count()).select_from(TeacherMockReceipt)) == 0


def test_crash_after_receipt_recovers_without_second_send(staff_system):
    client, system = staff_system
    ticket_id, _ = queue_reply(client, system)
    worker = TeacherReplyWorker(system["sessions"], max_attempts=1)
    claim = worker.claim()
    receipt_id = worker.send(claim)
    with system["sessions"].begin() as session:
        row = session.get(TeacherReplyOutbox, claim.outbox_id)
        row.lease_expires_at = session.scalar(select(func.now())) - timedelta(seconds=1)
    replacement = worker.claim()
    assert worker.send(replacement) == receipt_id
    assert worker.finish(replacement, status="SENT") == "SENT"
    with system["sessions"]() as session:
        assert session.get(Ticket, ticket_id).status == "RESOLVED"
        assert session.scalar(select(func.count()).select_from(TeacherMockReceipt)) == 1


def test_exhausted_delivery_stays_unresolved(staff_system, monkeypatch):
    client, system = staff_system
    ticket_id, _ = queue_reply(client, system)
    worker = TeacherReplyWorker(system["sessions"], max_attempts=1)

    def broken(_):
        raise RuntimeError("sensitive exception must not become error text")

    monkeypatch.setattr(worker, "send", broken)
    assert worker.run_once() == ["DEAD"]
    with system["sessions"]() as session:
        assert session.get(Ticket, ticket_id).status == "REPLY_QUEUED"
        assert (
            session.scalar(select(TeacherReplyOutbox)).last_error_code == "TEACHER_DELIVERY_FAILURE"
        )


def test_stale_and_changed_idempotency_requests_have_no_side_effect(staff_system):
    client, system = staff_system
    path = f"/internal/teacher/tickets/{system['tickets']['AUTO-N8N-01']}"
    command = {"expected_version": 1, "idempotency_key": "claim-version-test"}
    assert client.post(path + "/claim", headers=headers(system), json=command).status_code == 200
    assert client.post(path + "/claim", headers=headers(system), json=command).status_code == 200
    assert (
        client.post(
            path + "/claim", headers=headers(system), json={**command, "expected_version": 2}
        ).status_code
        == 409
    )
    assert (
        client.post(
            path + "/reply-draft",
            headers=headers(system),
            json={
                "expected_version": 1,
                "idempotency_key": "draft-stale-test",
                "body": "A bounded teacher response for this synthetic ticket.",
            },
        ).status_code
        == 409
    )
    with system["sessions"]() as session:
        assert session.scalar(select(func.count()).select_from(TicketAction)) == 1


def test_close_does_not_claim_resolution_or_send_a_reply(staff_system):
    client, system = staff_system
    ticket_id = system["tickets"]["AUTO-N8N-01"]
    path = f"/internal/teacher/tickets/{ticket_id}/close"
    command = {"expected_version": 1, "idempotency_key": "close-no-reply-test", "reason": ""}
    assert client.post(path, headers=headers(system), json=command).status_code == 422
    result = client.post(
        path,
        headers=headers(system),
        json={**command, "reason": "Duplicate question handled in the classroom."},
    )
    assert result.status_code == 200
    assert result.json()["status"] == "CLOSED_NO_REPLY"
    with system["sessions"]() as session:
        assert session.get(Ticket, ticket_id).resolved_at is None
        assert session.scalar(select(func.count()).select_from(TeacherReplyOutbox)) == 0


def test_assigned_teacher_can_release_unapproved_ticket(staff_system):
    client, system = staff_system
    path = f"/internal/teacher/tickets/{system['tickets']['AUTO-N8N-01']}"
    assert (
        client.post(
            path + "/claim",
            headers=headers(system),
            json={"expected_version": 1, "idempotency_key": "release-claim-test"},
        ).status_code
        == 200
    )
    released = client.post(
        path + "/release",
        headers=headers(system),
        json={"expected_version": 2, "idempotency_key": "release-action-test"},
    )
    assert released.status_code == 200
    assert released.json()["status"] == "OPEN"
    assert released.json()["assigned_teacher_id"] is None


def test_staff_metrics_do_not_include_another_class(staff_system):
    client, system = staff_system
    queue_reply(client, system)
    assert (
        client.get("/internal/teacher/metrics", headers=headers(system, "fixture-gs")).json()[
            "teacher_outbox"
        ]
        == {}
    )
    assert client.get("/internal/teacher/metrics", headers=headers(system)).json()[
        "teacher_outbox"
    ] == {"PENDING": 1}
