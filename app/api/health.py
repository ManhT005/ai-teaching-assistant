"""Liveness is independent of DB; readiness checks DB and pgvector."""
from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from app.db import get_engine

router = APIRouter(tags=["system"])


@router.get("/health")
def health():
    return {"status": "ok", "service": "ai-teaching-assistant"}


@router.get("/ready")
def ready():
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
            installed = conn.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')"))
            if not installed:
                raise RuntimeError("pgvector extension is not enabled")
    except Exception:
        raise HTTPException(status_code=503, detail="Database is not ready") from None
    return {"status": "ready", "database": "postgresql", "vector": True}
