"""Minimal synchronous SQLAlchemy setup. No DB access during import."""
from functools import lru_cache
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from app.config import get_settings


@lru_cache(maxsize=1)
def get_engine():
    url = get_settings().database_url
    if not url or "REPLACE_WITH_" in url:
        raise RuntimeError("DATABASE_URL must be configured in .env")
    return create_engine(url, pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_session_factory():
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    with get_session_factory()() as session:
        yield session
