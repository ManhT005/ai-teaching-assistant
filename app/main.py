"""Scoped teaching demo and authenticated staff workflow; explicit migrations."""

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.api.demo_admin import router as demo_admin_router
from app.api.faq import router as faq_router
from app.api.health import router as health_router
from app.api.internal_demo import router as internal_demo_router
from app.api.teacher import router as teacher_router
from app.api.webhook import router as webhook_router
from app.teacher.contracts import WorkflowError

app = FastAPI(
    title="GOS Academy AI Teaching Assistant",
    description="Class-scoped grounded teaching, human handoff and reviewed FAQ; mock delivery.",
    version="0.1.0",
)
app.include_router(health_router)
app.include_router(demo_admin_router)
app.include_router(internal_demo_router)
app.include_router(webhook_router)
app.include_router(teacher_router)
app.include_router(faq_router)


@app.exception_handler(WorkflowError)
async def workflow_error_handler(request, error: WorkflowError):
    return JSONResponse(status_code=error.status, content={"detail": error.code})
