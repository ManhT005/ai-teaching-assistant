"""Serve the local demo console shell. Data APIs authenticate independently."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

_ASSETS = Path(__file__).resolve().parents[1] / "demo_admin"
_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}
router = APIRouter(prefix="/demo/admin", include_in_schema=False)


@router.get("")
def demo_admin():
    return FileResponse(_ASSETS / "index.html", headers=_HEADERS)


@router.get("/style.css")
def demo_admin_style():
    return FileResponse(_ASSETS / "style.css", media_type="text/css", headers=_HEADERS)


@router.get("/console.js")
def demo_admin_script():
    return FileResponse(_ASSETS / "console.js", media_type="text/javascript", headers=_HEADERS)
