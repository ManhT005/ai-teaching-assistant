"""Internal FAQ authoring/review APIs and operator-controlled publication."""

# ruff: noqa: B008 -- FastAPI dependency declarations

from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db import get_db
from app.faq.contracts import FAQContent, FAQCreate, FAQReview, FAQRevoke
from app.faq.publisher import FAQPublisher
from app.faq.service import create_candidate, edit_candidate, faq_view, scoped_candidate
from app.knowledge.embeddings import EmbeddingService
from app.knowledge.store import KnowledgeStore
from app.teacher.auth import staff_identity
from app.teacher.contracts import Command

router = APIRouter(prefix="/internal/faq", tags=["faq"])


def faq_publisher(db=Depends(get_db)):
    sessions = sessionmaker(db.get_bind(), expire_on_commit=False)
    return FAQPublisher(
        sessions, KnowledgeStore(sessions, EmbeddingService()), Path(get_settings().faq_output_dir)
    )


@router.post("/candidates")
def propose_faq(command: FAQCreate, identity=Depends(staff_identity), db=Depends(get_db)):
    result = create_candidate(db, identity, command)
    db.commit()
    return result


@router.get("/candidates/{faq_id}")
def get_faq(faq_id: int, identity=Depends(staff_identity), db=Depends(get_db)):
    return faq_view(db, scoped_candidate(db, identity, faq_id))


@router.patch("/candidates/{faq_id}/draft")
def revise_faq(
    faq_id: int, command: FAQContent, identity=Depends(staff_identity), db=Depends(get_db)
):
    result = edit_candidate(db, identity, faq_id, "draft", command)
    db.commit()
    return result


@router.post("/candidates/{faq_id}/submit")
def submit_faq(faq_id: int, command: Command, identity=Depends(staff_identity), db=Depends(get_db)):
    result = edit_candidate(db, identity, faq_id, "submit", command)
    db.commit()
    return result


@router.post("/candidates/{faq_id}/review")
def review_faq(
    faq_id: int, command: FAQReview, identity=Depends(staff_identity), db=Depends(get_db)
):
    result = edit_candidate(db, identity, faq_id, "review", command)
    db.commit()
    return result


@router.post("/candidates/{faq_id}/publish")
def publish_faq(
    faq_id: int,
    command: Command,
    identity=Depends(staff_identity),
    publisher=Depends(faq_publisher),
    db=Depends(get_db),
):
    # Release the authentication query's connection before embedding. The
    # publisher rechecks identity in its own short transactions.
    db.rollback()
    return publisher.publish(identity, faq_id, command)


@router.post("/candidates/{faq_id}/revoke")
def revoke_faq(
    faq_id: int,
    command: FAQRevoke,
    identity=Depends(staff_identity),
    publisher=Depends(faq_publisher),
):
    return publisher.revoke(identity, faq_id, command)
