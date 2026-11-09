"""DB-admin CLI to grant a real local staff actor a random expiring credential."""

import argparse
import os
import re
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select

from app.db import get_session_factory
from app.models import Classroom
from app.teacher.auth import issue_credential
from app.workflow_models import StaffActor, StaffClassGrant, StaffCredential


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alias")
    parser.add_argument("--class-code", choices=("GS-KPI-01", "AUTO-N8N-01"))
    parser.add_argument("--role", action="append", choices=("TEACHER", "FAQ_REVIEWER", "OPERATOR"))
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--revoke-credential-id", type=int)
    args = parser.parse_args(argv)
    sessions = get_session_factory()
    if args.revoke_credential_id:
        with sessions.begin() as session:
            credential = session.get(
                StaffCredential, args.revoke_credential_id, with_for_update=True
            )
            if credential is None:
                parser.error("Credential not found")
            credential.revoked_at = session.scalar(select(func.now()))
        print("Credential revoked")
        return
    if (
        not args.alias
        or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", args.alias)
        or not args.class_code
        or not args.role
        or not 1 <= args.days <= 90
    ):
        parser.error("Provide --alias, --class-code, --role and --days between 1 and 90")
    directory = Path(".local/staff-credentials")
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{args.alias}-{uuid4().hex}.token"
    with sessions.begin() as session:
        classroom = session.scalar(select(Classroom).where(Classroom.code == args.class_code))
        if classroom is None:
            parser.error("Seed classrooms first")
        actor = session.scalar(select(StaffActor).where(StaffActor.alias == args.alias))
        if actor is None:
            actor = StaffActor(alias=args.alias, active=True)
            session.add(actor)
            session.flush()
        if not actor.active:
            parser.error("Actor is disabled")
        for role in set(args.role):
            exists = session.scalar(
                select(StaffClassGrant.id).where(
                    StaffClassGrant.actor_id == actor.id,
                    StaffClassGrant.class_id == classroom.id,
                    StaffClassGrant.role == role,
                )
            )
            if exists is None:
                session.add(StaffClassGrant(actor_id=actor.id, class_id=classroom.id, role=role))
        token = issue_credential(session, actor.id, days=args.days)
        with os.fdopen(
            os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8"
        ) as output:
            output.write(token + "\n")
        actor_id = actor.id
    print(f"Staff actor {actor_id}; credential written to {destination}. Keep this file private.")


if __name__ == "__main__":
    main()
