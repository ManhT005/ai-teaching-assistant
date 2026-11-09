"""Server-owned generic headings around the existing verbatim quote output."""

from app.context.contracts import TeachingIntent

_HEADINGS = {
    TeachingIntent.STANDALONE: "Trích dẫn liên quan",
    TeachingIntent.NEW_TOPIC: "Trích dẫn liên quan",
    TeachingIntent.FOLLOWUP_EXPLANATION: "Giải thích từ tài liệu",
    TeachingIntent.FOLLOWUP_EXAMPLE: "Ví dụ từ tài liệu",
    TeachingIntent.FOLLOWUP_REFERENCE: "Thông tin liên quan từ tài liệu",
}


def format_teaching_reply(intent: TeachingIntent, quoted_answer: str) -> str:
    return f"{_HEADINGS[intent]}\n{quoted_answer}"
