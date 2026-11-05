"""Local fixture bindings and trigger syntax; never derive runtime scope from a client class ID."""
import re

DEMO_CLASSES = (
    ("GS-KPI-01", "Google Sheets KPI Dashboard", "mock-google-sheets"),
    ("AUTO-N8N-01", "AI Automation n8n", "mock-ai-automation"),
)
DEMO_GROUP_BY_CLASS = {code: group for code, _, group in DEMO_CLASSES}


def question_from_text(text: str) -> str | None:
    match = re.match(r"^\s*/hoi(?:\s+|$)(.*)$", text, re.DOTALL)
    if match and match.group(1).strip():
        return match.group(1).strip()
    return None
