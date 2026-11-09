"""Safe operator preview for persisted tickets; no outbound delivery is implied."""


def insufficient_evidence_message() -> str:
    return (
        "Mình chưa tìm thấy đủ căn cứ trong học liệu của lớp để trả lời đầy đủ câu hỏi này. "
        "Câu hỏi cần giảng viên xác nhận thêm."
    )


def handoff_acknowledgment() -> str:
    return "Yêu cầu đã được ghi nhận để giảng viên xem xét thêm."
