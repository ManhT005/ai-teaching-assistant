"""Random staff credentials are stored as SHA-256 digests, never plaintext."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, Header
from sqlalchemy import func, select

from app.db import get_db
from app.teacher.contracts import WorkflowError
from app.workflow_models import StaffActor, StaffClassGrant, StaffCredential


@dataclass(frozen=True)
class StaffIdentity:
    actor_id: int
    credential_id: int


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_credential(session, actor_id: int, *, days=30) -> str:
    token = secrets.token_urlsafe(32)
    session.add(
        StaffCredential(
            actor_id=actor_id,
            token_hash=token_digest(token),
            expires_at=session.scalar(select(func.now())) + timedelta(days=days),
        )
    )
    return token


def authenticate(session, token: str | None) -> StaffIdentity:
    if not token or len(token) > 200:
        raise WorkflowError("STAFF_UNAUTHORIZED", 401)
    row = session.execute(
        select(StaffActor.id, StaffCredential.id)
        .join(StaffCredential, StaffCredential.actor_id == StaffActor.id)
        .where(
            StaffCredential.token_hash == token_digest(token),
            StaffActor.active.is_(True),
            StaffCredential.revoked_at.is_(None),
            StaffCredential.expires_at > func.now(),
        )
    ).one_or_none()
    if row is None:
        raise WorkflowError("STAFF_UNAUTHORIZED", 401)
    return StaffIdentity(row[0], row[1])


def active_identity(session, identity: StaffIdentity) -> None:
    exists = session.scalar(
        select(StaffCredential.id)
        .join(StaffActor, StaffActor.id == StaffCredential.actor_id)
        .where(
            StaffCredential.id == identity.credential_id,
            StaffCredential.actor_id == identity.actor_id,
            StaffCredential.revoked_at.is_(None),
            StaffCredential.expires_at > func.now(),
            StaffActor.active.is_(True),
        )
        .with_for_update(read=True, of=(StaffActor, StaffCredential))
    )
    if exists is None:
        raise WorkflowError("STAFF_UNAUTHORIZED", 401)


def granted_classes(session, identity: StaffIdentity, roles: tuple[str, ...]):
    active_identity(session, identity)
    return select(StaffClassGrant.class_id).where(
        StaffClassGrant.actor_id == identity.actor_id, StaffClassGrant.role.in_(roles)
    )


def require_role(session, identity: StaffIdentity, class_id: int, roles: tuple[str, ...]):
    classes = granted_classes(session, identity, roles)
    if (
        session.scalar(
            select(StaffClassGrant.id)
            .where(
                StaffClassGrant.class_id == class_id,
                StaffClassGrant.actor_id == identity.actor_id,
                StaffClassGrant.role.in_(roles),
                StaffClassGrant.class_id.in_(classes),
            )
            .with_for_update(read=True)
        )
        is None
    ):
        raise WorkflowError("WORKFLOW_NOT_FOUND", 404)


def staff_identity(x_teacher_token: str | None = Header(default=None), db=Depends(get_db)):  # noqa: B008
    return authenticate(db, x_teacher_token)
