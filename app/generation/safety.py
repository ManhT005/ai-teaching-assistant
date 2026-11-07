"""Bounded demo safety rules, not a complete injection or secret detector."""

import re
import unicodedata


def folded(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.casefold()).replace("đ", "d")
    return "".join(char for char in normalized if not unicodedata.combining(char))


UNSAFE_PATTERNS = (
    r"(?:ignore|override|disregard).{0,60}(?:instruction|policy|previous|system)",
    r"(?:bo qua|quen|ghi de).{0,60}(?:chi thi|chinh sach|quy tac|huong dan)",
    (
        r"(?:reveal|print|show|expose|tiet lo|in ra|hien thi).{0,60}"
        r"(?:system prompt|secret|api.?key|mat khau|token|khoa bi mat)"
    ),
    r"(?<!khong )(?<!bao gio )\bgui.{0,60}(?:system prompt|secret|api.?key|mat khau|token)",
    r"(?:api[_-]?key|token|password|secret)\s*[:=]\s*['\"]?[a-z0-9_/-]{12,}",
    r"\b(?:sk-[a-z0-9_-]{16,}|AIza[a-z0-9_-]{20,})\b",
    r"<\s*/?\s*(?:system|assistant|developer)\b",
)


def is_unsafe(text: str) -> bool:
    value = folded(text)
    return any(re.search(pattern, value) for pattern in UNSAFE_PATTERNS)


def is_sensitive_policy(text: str) -> bool:
    return bool(
        re.search(
            r"hoc phi|giam gia|khuyen mai|hoan tien|chinh sach|so dien thoai|"
            r"dia chi nha|can cuoc|luong giao vien|tuition|discount|refund",
            folded(text),
        )
    )
