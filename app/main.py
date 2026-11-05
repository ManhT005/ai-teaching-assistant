"""FastAPI entrypoint — Day 1 skeleton, no automatic schema creation."""
from fastapi import FastAPI
from app.api.health import router as health_router
from app.api.webhook import router as webhook_router

app = FastAPI(
    title="GOS Academy AI Teaching Assistant",
    description="Lean MVP skeleton; no real Zalo or AI answers on Day 1.",
    version="0.1.0",
)
app.include_router(health_router)
app.include_router(webhook_router)
