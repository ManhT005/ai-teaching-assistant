import hashlib

import pytest
from openpyxl import load_workbook
from sqlalchemy import func, select
from teacher_faq_helpers import headers, resolved_candidate, reviewed_candidate
from test_generation_events import add_event

from app.delivery.dispatcher import DeliveryWorker
from app.delivery.mock_adapter import MockReplyAdapter
from app.events.processor import EventProcessor
from app.knowledge.source_manifest import load_manifest
from app.models import Document
from app.retrieval.service import HybridRetrievalService
from app.workflow_models import FAQCandidate, FAQRevision, TicketAction


def test_reply_and_unapproved_candidate_do_not_enter_index(staff_system):
    client, system = staff_system
    candidate = resolved_candidate(client, system)
    path = f"/internal/faq/candidates/{candidate['faq_id']}"
    assert (
        client.post(
            path + "/publish",
            headers=headers(system),
            json={"expected_version": 1, "idempotency_key": "publish-pending"},
        ).status_code
        == 409
    )
    with system["sessions"]() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Document)
                .where(Document.logical_key.like("teacher-faq-%"))
            )
            == 0
        )


def test_independent_reviewer_and_hash_are_mandatory(staff_system):
    client, system = staff_system
    candidate = resolved_candidate(client, system)
    path = f"/internal/faq/candidates/{candidate['faq_id']}"
    assert (
        client.post(
            path + "/submit",
            headers=headers(system),
            json={"expected_version": 1, "idempotency_key": "faq-submit-test"},
        ).status_code
        == 200
    )
    review = {
        "expected_version": 1,
        "idempotency_key": "faq-review-test",
        "decision": "approve",
        "candidate_body_hash": candidate["body_hash"],
        "reason": "Fixture-only review; not real human approval",
    }
    assert client.post(path + "/review", headers=headers(system), json=review).status_code == 403
    assert (
        client.post(
            path + "/review", headers=headers(system, "fixture-gs"), json=review
        ).status_code
        == 404
    )
    assert (
        client.post(
            path + "/review",
            headers=headers(system, "fixture-reviewer"),
            json={**review, "candidate_body_hash": "0" * 64},
        ).status_code
        == 409
    )
    assert client.get(path, headers=headers(system, "fixture-gs")).status_code == 404
    assert (
        client.post(
            path + "/review", headers=headers(system, "fixture-reviewer"), json=review
        ).status_code
        == 200
    )
    assert (
        client.post(
            path + "/review", headers=headers(system, "fixture-reviewer"), json=review
        ).status_code
        == 200
    )


def test_publish_retrieve_replace_and_revoke_with_exact_scoped_identity(staff_system):
    client, system = staff_system
    candidate = reviewed_candidate(client, system)
    path = f"/internal/faq/candidates/{candidate['faq_id']}"
    command = {"expected_version": 1, "idempotency_key": "faq-publish-test"}
    result = client.post(path + "/publish", headers=headers(system), json=command)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "PUBLISHED"
    first_document_id = result.json()["document_id"]
    assert client.post(path + "/publish", headers=headers(system), json=command).status_code == 200
    retrieval = HybridRetrievalService(system["sessions"], system["store"].embeddings)
    logical_key = f"teacher-faq-{candidate['faq_id']}"
    hits = retrieval.search("n8n HTTP Request", system["scopes"]["AUTO-N8N-01"]).hits
    assert any(
        hit.doc_id == logical_key and hit.version == 1 and "knowledge!row 2" in hit.locator
        for hit in hits
    )
    assert all(
        hit.doc_id != logical_key
        for hit in retrieval.search("n8n HTTP Request", system["scopes"]["GS-KPI-01"]).hits
    )

    draft = {
        "expected_version": 1,
        "idempotency_key": "faq-revise-test",
        "question": candidate["question"],
        "answer": "n8n HTTP Request sends the configured HTTP request to the selected target API.",
        "topics": ["HTTP"],
    }
    updated = client.patch(path + "/draft", headers=headers(system), json=draft)
    assert updated.status_code == 200, updated.text
    assert updated.json()["revision"] == 2
    with system["sessions"]() as session:
        assert session.get(Document, first_document_id).active
    assert (
        client.post(
            path + "/submit",
            headers=headers(system),
            json={"expected_version": 2, "idempotency_key": "faq-submit-vtwo"},
        ).status_code
        == 200
    )
    review = {
        "expected_version": 2,
        "idempotency_key": "faq-review-vtwo",
        "decision": "approve",
        "candidate_body_hash": updated.json()["body_hash"],
        "reason": "Synthetic second-version review only",
    }
    assert (
        client.post(
            path + "/review", headers=headers(system, "fixture-reviewer"), json=review
        ).status_code
        == 200
    )
    result = client.post(
        path + "/publish",
        headers=headers(system),
        json={"expected_version": 2, "idempotency_key": "faq-publish-vtwo"},
    )
    assert result.status_code == 200, result.text
    with system["sessions"]() as session:
        assert not session.get(Document, first_document_id).active
        assert session.get(Document, result.json()["document_id"]).version == 2
    event_id = add_event(system["sessions"], system["scopes"]["AUTO-N8N-01"], "n8n HTTP Request")
    answer = EventProcessor(system["sessions"], system["pipeline"]).process_event(event_id)
    assert answer.status == "ANSWERED"
    assert any(
        citation.doc_id == logical_key and citation.document_version == 2
        for citation in answer.citations
    )
    revoke = client.post(
        path + "/revoke",
        headers=headers(system),
        json={
            "expected_version": 2,
            "idempotency_key": "faq-revoke-test",
            "reason": "Synthetic withdrawal test",
        },
    )
    assert revoke.status_code == 200, revoke.text
    assert revoke.json()["status"] == "REVOKED"
    delivered = DeliveryWorker(system["sessions"], MockReplyAdapter(system["sessions"])).run_once()
    assert "SUPPRESSED" in delivered
    assert all(
        hit.doc_id != logical_key
        for hit in retrieval.search("n8n HTTP Request", system["scopes"]["AUTO-N8N-01"]).hits
    )


def test_failed_indexing_stays_inactive_and_retry_is_controlled(staff_system, monkeypatch):
    client, system = staff_system
    candidate = reviewed_candidate(client, system)
    path = f"/internal/faq/candidates/{candidate['faq_id']}"
    command = {"expected_version": 1, "idempotency_key": "faq-publish-failure"}
    original = system["store"].embeddings.encode_passages

    def failed(_):
        raise RuntimeError("must not leak source or provider details")

    monkeypatch.setattr(system["store"].embeddings, "encode_passages", failed)
    result = client.post(path + "/publish", headers=headers(system), json=command)
    assert result.status_code == 503, result.text
    assert "provider details" not in result.text
    with system["sessions"]() as session:
        assert not session.scalar(
            select(Document.active).where(
                Document.logical_key == f"teacher-faq-{candidate['faq_id']}"
            )
        )
        assert session.get(FAQCandidate, candidate["faq_id"]).status == "FAILED"
    monkeypatch.setattr(system["store"].embeddings, "encode_passages", original)
    assert client.post(path + "/publish", headers=headers(system), json=command).status_code == 200


def test_artifact_manifest_tampering_and_formula_cells_are_rejected(staff_system):
    client, system = staff_system
    candidate = reviewed_candidate(client, system)
    path = f"/internal/faq/candidates/{candidate['faq_id']}"
    assert (
        client.post(
            path + "/publish",
            headers=headers(system),
            json={"expected_version": 1, "idempotency_key": "faq-publish-export"},
        ).status_code
        == 200
    )
    manifest = next(system["publisher"].output_dir.glob("*.manifest.json"))
    artifact = next(system["publisher"].output_dir.glob("*.xlsx"))
    original = artifact.read_bytes()
    artifact.write_bytes(original + b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        load_manifest(manifest)
    artifact.write_bytes(original)
    workbook = load_workbook(artifact)
    workbook["knowledge"]["D2"] = '=HYPERLINK("https://invalid.example")'
    workbook.save(artifact)
    workbook.close()
    # Even a recomputed outer checksum cannot bypass the parser's formula gate.
    import json

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["sources"][0]["sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="Formulas"):
        load_manifest(manifest)


@pytest.mark.parametrize(
    "body",
    [
        "=SUM(A1:A2)",
        "Tuition refund is always guaranteed.",
        "token=synthetic-secret-value-123456",
        "<b>unsafe markup</b>",
    ],
)
def test_restricted_faq_content_is_blocked(staff_system, body):
    client, system = staff_system
    candidate = resolved_candidate(client, system)
    result = client.patch(
        f"/internal/faq/candidates/{candidate['faq_id']}/draft",
        headers=headers(system),
        json={
            "expected_version": 1,
            "idempotency_key": "faq-invalid-text",
            "question": "n8n HTTP",
            "answer": body,
        },
    )
    assert result.status_code == 422


def test_publication_releases_request_auth_connection_during_embedding(staff_system, monkeypatch):
    client, system = staff_system
    candidate = reviewed_candidate(client, system)
    original = system["store"].embeddings.encode_passages
    calls = []

    def checked(texts):
        calls.append(True)
        assert system["sessions"].kw["bind"].pool.checkedout() == 0
        return original(texts)

    monkeypatch.setattr(system["store"].embeddings, "encode_passages", checked)
    result = client.post(
        f"/internal/faq/candidates/{candidate['faq_id']}/publish",
        headers=headers(system),
        json={"expected_version": 1, "idempotency_key": "publish-no-db-held"},
    )
    assert result.status_code == 200, result.text
    assert calls


def test_workflow_downgrade_withdraws_published_faq_and_keeps_event(staff_system):
    from conftest import migrate
    from sqlalchemy import text

    client, system = staff_system
    candidate = reviewed_candidate(client, system)
    result = client.post(
        f"/internal/faq/candidates/{candidate['faq_id']}/publish",
        headers=headers(system),
        json={"expected_version": 1, "idempotency_key": "publish-rollback-test"},
    )
    assert result.status_code == 200
    engine = system["sessions"].kw["bind"]
    migrate(engine, "0008_contextual_teaching", downgrade=True)
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT active FROM documents WHERE id=:id"),
                {"id": result.json()["document_id"]},
            )
            is False
        )
        assert connection.scalar(text("SELECT count(*) FROM events WHERE status='HANDOFF'")) == 2
    migrate(engine)


def test_urgent_withdrawal_is_allowed_after_origin_binding_is_disabled(staff_system):
    from app.models import ChannelBinding

    client, system = staff_system
    candidate = reviewed_candidate(client, system)
    path = f"/internal/faq/candidates/{candidate['faq_id']}"
    result = client.post(
        path + "/publish",
        headers=headers(system),
        json={"expected_version": 1, "idempotency_key": "publish-rebind-test"},
    )
    assert result.status_code == 200
    with system["sessions"].begin() as session:
        session.get(ChannelBinding, system["scopes"]["AUTO-N8N-01"].binding_id).active = False
    result = client.post(
        path + "/revoke",
        headers=headers(system),
        json={
            "expected_version": 1,
            "idempotency_key": "revoke-disabled-origin",
            "reason": "Synthetic urgent withdrawal after binding disabled",
        },
    )
    assert result.status_code == 200, result.text
    with system["sessions"]() as session:
        assert (
            session.scalar(
                select(Document.active).where(
                    Document.logical_key == f"teacher-faq-{candidate['faq_id']}"
                )
            )
            is False
        )


def test_audit_is_append_only(staff_system):
    client, system = staff_system
    resolved_candidate(client, system)
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError), system["sessions"].begin() as session:
        row = session.scalar(select(TicketAction))
        row.action = "tampered"


def test_reviewed_content_is_database_immutable(staff_system):
    from sqlalchemy.exc import DBAPIError

    client, system = staff_system
    candidate = reviewed_candidate(client, system)
    with pytest.raises(DBAPIError), system["sessions"].begin() as session:
        revision = session.scalar(
            select(FAQRevision).where(FAQRevision.faq_id == candidate["faq_id"])
        )
        revision.answer = "Changed after approval without independent review."


def test_rejected_candidate_never_becomes_active(staff_system):
    client, system = staff_system
    candidate = resolved_candidate(client, system)
    path = f"/internal/faq/candidates/{candidate['faq_id']}"
    assert (
        client.post(
            path + "/submit",
            headers=headers(system),
            json={"expected_version": 1, "idempotency_key": "reject-submit-test"},
        ).status_code
        == 200
    )
    result = client.post(
        path + "/review",
        headers=headers(system, "fixture-reviewer"),
        json={
            "expected_version": 1,
            "idempotency_key": "reject-review-test",
            "decision": "reject",
            "candidate_body_hash": candidate["body_hash"],
            "reason": "Synthetic content requires a corrected example.",
        },
    )
    assert result.status_code == 200 and result.json()["status"] == "REJECTED"
    assert (
        client.post(
            path + "/publish",
            headers=headers(system),
            json={"expected_version": 1, "idempotency_key": "reject-publish-test"},
        ).status_code
        == 409
    )


@pytest.mark.parametrize("withdrawal", ["reviewer", "candidate"])
def test_revocation_during_embedding_blocks_atomic_activation(
    staff_system, monkeypatch, withdrawal
):
    from app.faq.contracts import FAQRevoke
    from app.workflow_models import StaffCredential

    client, system = staff_system
    candidate = reviewed_candidate(client, system)
    original = system["store"].embeddings.encode_passages

    def revoked_during_embedding(texts):
        if withdrawal == "reviewer":
            with system["sessions"].begin() as session:
                credential = session.get(
                    StaffCredential, system["identities"]["fixture-reviewer"].credential_id
                )
                credential.revoked_at = session.scalar(select(func.now()))
        else:
            system["publisher"].revoke(
                system["identities"]["fixture-author"],
                candidate["faq_id"],
                FAQRevoke(
                    expected_version=1,
                    idempotency_key="revoke-during-index",
                    reason="Synthetic urgent withdrawal",
                ),
            )
        return original(texts)

    monkeypatch.setattr(system["store"].embeddings, "encode_passages", revoked_during_embedding)
    result = client.post(
        f"/internal/faq/candidates/{candidate['faq_id']}/publish",
        headers=headers(system),
        json={"expected_version": 1, "idempotency_key": "publish-index-race"},
    )
    assert result.status_code == 503
    with system["sessions"]() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Document)
                .where(
                    Document.logical_key == f"teacher-faq-{candidate['faq_id']}",
                    Document.active.is_(True),
                )
            )
            == 0
        )
