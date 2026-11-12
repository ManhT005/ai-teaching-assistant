"""Separate fenced teacher outbox and durable idempotent local mock receipts."""

from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, or_, select

from app.events.retry import RetryPolicy
from app.models import ChannelBinding, Classroom, Ticket
from app.teacher.auth import StaffIdentity, active_identity, require_role
from app.teacher.contracts import WorkflowError, clean_text, digest
from app.teacher.service import origin_scope
from app.workflow_models import (
    StaffActor,
    StaffCredential,
    TeacherMockReceipt,
    TeacherReply,
    TeacherReplyOutbox,
    TicketAction,
)


@dataclass(frozen=True)
class TeacherDeliveryClaim:
    outbox_id: int
    lease_token: str


class TeacherReplyWorker:
    def __init__(self, sessions, *, lease_seconds=60, max_attempts=3):
        if lease_seconds < 1 or max_attempts < 1:
            raise ValueError("Positive delivery bounds required")
        self.sessions = sessions
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.retry = RetryPolicy(base_seconds=2, cap_seconds=60)

    def claim(self):
        with self.sessions.begin() as session:
            now = session.scalar(select(func.now()))
            row = session.scalar(
                select(TeacherReplyOutbox)
                .where(
                    or_(
                        TeacherReplyOutbox.status.in_(("PENDING", "RETRY_WAIT"))
                        & or_(
                            TeacherReplyOutbox.next_attempt_at.is_(None),
                            TeacherReplyOutbox.next_attempt_at <= now,
                        ),
                        (TeacherReplyOutbox.status == "SENDING")
                        & (TeacherReplyOutbox.lease_expires_at <= now),
                    )
                )
                .order_by(TeacherReplyOutbox.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                return None
            # An accepted receipt is recoverable even after the last send attempt.
            receipt = session.scalar(
                select(TeacherMockReceipt.id).where(TeacherMockReceipt.teacher_outbox_id == row.id)
            )
            if row.attempts >= self.max_attempts and receipt is None:
                row.status, row.last_error_code = "DEAD", "TEACHER_RETRIES_EXHAUSTED"
                row.lease_token, row.lease_expires_at = None, None
                return None
            row.status, row.lease_token = "SENDING", str(uuid4())
            row.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            row.attempts += 1
            return TeacherDeliveryClaim(row.id, row.lease_token)

    @staticmethod
    def _owned(row, claim, now):
        return (
            row is not None
            and row.status == "SENDING"
            and row.lease_token == claim.lease_token
            and row.lease_expires_at > now
        )

    def _locked_rows(self, session, claim):
        reply_id = session.scalar(
            select(TeacherReplyOutbox.reply_id).where(TeacherReplyOutbox.id == claim.outbox_id)
        )
        ticket_id = session.scalar(
            select(TeacherReply.ticket_id).where(TeacherReply.id == reply_id)
        )
        ticket = session.get(Ticket, ticket_id, with_for_update=True) if ticket_id else None
        reply = session.get(TeacherReply, reply_id, with_for_update=True) if reply_id else None
        row = session.get(TeacherReplyOutbox, claim.outbox_id, with_for_update=True)
        if not self._owned(row, claim, session.scalar(select(func.now()))):
            raise WorkflowError("TEACHER_LEASE_LOST")
        return ticket, reply, row

    def send(self, claim):
        with self.sessions.begin() as session:
            ticket, reply, row = self._locked_rows(session, claim)
            existing = session.scalar(
                select(TeacherMockReceipt.id).where(TeacherMockReceipt.teacher_outbox_id == row.id)
            )
            if existing is not None:
                return existing
            if (
                ticket.status != "REPLY_QUEUED"
                or reply.status != "APPROVED"
                or reply.revision != row.approved_revision
                or reply.body_hash != row.approved_hash
                or digest(reply.body) != row.approved_hash
            ):
                raise WorkflowError("TEACHER_REPLY_SUPPRESSED")
            for model, object_id in (
                (Classroom, row.class_id),
                (ChannelBinding, row.binding_id),
                (StaffActor, reply.approver_actor_id),
                (StaffCredential, reply.approval_credential_id),
            ):
                session.execute(
                    select(model.id).where(model.id == object_id).with_for_update(read=True)
                ).all()
            _, scope = origin_scope(session, ticket)
            if (scope.binding_id, scope.class_id, scope.channel, scope.group_id) != (
                row.binding_id,
                row.class_id,
                row.channel,
                row.group_id,
            ):
                raise WorkflowError("TEACHER_REPLY_SUPPRESSED")
            identity = StaffIdentity(reply.approver_actor_id, reply.approval_credential_id)
            active_identity(session, identity)
            require_role(session, identity, ticket.class_id, ("TEACHER",))
            clean_text(reply.body)
            receipt = TeacherMockReceipt(
                teacher_outbox_id=row.id,
                message_key=row.message_key,
                body="Phản hồi của giảng viên:\n" + reply.body,
            )
            session.add(receipt)
            session.flush()
            return receipt.id

    def finish(self, claim, *, status, error=None):
        with self.sessions.begin() as session:
            ticket, reply, row = self._locked_rows(session, claim)
            now = session.scalar(select(func.now()))
            if status == "SENT":
                receipt = session.scalar(
                    select(TeacherMockReceipt.id).where(
                        TeacherMockReceipt.teacher_outbox_id == row.id,
                        TeacherMockReceipt.message_key == row.message_key,
                    )
                )
                if receipt is None:
                    raise WorkflowError("TEACHER_RECEIPT_REQUIRED")
                row.sent_at, ticket.resolved_at = now, now
                session.add(
                    TicketAction(
                        ticket_id=ticket.id,
                        actor_id=reply.approver_actor_id,
                        action="teacher-mock-receipt",
                        from_state=ticket.status,
                        to_state="RESOLVED",
                        expected_version=ticket.version,
                        action_key=f"teacher-receipt:{row.id}",
                        request_hash=digest(
                            {"message_key": row.message_key, "receipt_id": receipt}
                        ),
                    )
                )
                ticket.status, reply.status = "RESOLVED", "SENT"
                ticket.version += 1
                ticket.last_action_at = now
            row.status, row.last_error_code = status, error
            row.next_attempt_at = (
                now + timedelta(seconds=self.retry.delay_seconds(row.attempts))
                if status == "RETRY_WAIT"
                else None
            )
            row.lease_token, row.lease_expires_at = None, None
            return status

    def run_once(self, *, limit=10):
        if not 1 <= limit <= 100:
            raise ValueError("Worker limit must be between 1 and 100")
        statuses = []
        for _ in range(limit):
            claim = self.claim()
            if claim is None:
                break
            try:
                self.send(claim)
                status = self.finish(claim, status="SENT")
            except WorkflowError as error:
                if error.code == "TEACHER_LEASE_LOST":
                    continue
                status = self.finish(
                    claim, status="SUPPRESSED", error="TEACHER_AUTH_OR_CONTENT_REVOKED"
                )
            except Exception:  # noqa: BLE001 -- never log exception contents or human text
                with self.sessions() as session:
                    attempts = session.get(TeacherReplyOutbox, claim.outbox_id).attempts
                try:
                    status = self.finish(
                        claim,
                        status="RETRY_WAIT" if attempts < self.max_attempts else "DEAD",
                        error="TEACHER_DELIVERY_FAILURE",
                    )
                except WorkflowError:
                    continue
            statuses.append(status)
        return statuses
