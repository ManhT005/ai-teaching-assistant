"""Server-owned generic headings around the existing verbatim quote output."""

from app.context.contracts import TeachingIntent
from app.teaching.contracts import PresentationIntent

_HEADINGS = {
    TeachingIntent.STANDALONE: "Trích dẫn liên quan",
    TeachingIntent.NEW_TOPIC: "Trích dẫn liên quan",
    TeachingIntent.FOLLOWUP_EXPLANATION: "Giải thích từ tài liệu",
    TeachingIntent.FOLLOWUP_EXAMPLE: "Ví dụ từ tài liệu",
    TeachingIntent.FOLLOWUP_REFERENCE: "Thông tin liên quan từ tài liệu",
}

_PRESENTATION_HEADINGS = {
    PresentationIntent.EXPLAIN: "Giải thích từ tài liệu",
    PresentationIntent.HOWTO: "Hướng dẫn từ tài liệu",
    PresentationIntent.TROUBLESHOOT: "Các bước kiểm tra từ tài liệu",
    PresentationIntent.COMPARE: "So sánh từ tài liệu",
    PresentationIntent.EXAMPLE: "Ví dụ từ tài liệu",
}


def format_planned_reply(intent: PresentationIntent, quoted_answer: str) -> str:
    return f"{_PRESENTATION_HEADINGS[intent]}\n{quoted_answer}"


def format_teaching_reply(intent: TeachingIntent, quoted_answer: str) -> str:
    return f"{_HEADINGS[intent]}\n{quoted_answer}"
