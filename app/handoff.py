"""Response text for a future persisted teacher ticket; does not claim a ticket was sent."""


def insufficient_evidence_message() -> str:
    return (
        "Mình chưa tìm thấy đủ căn cứ trong học liệu của lớp để trả lời đầy đủ câu hỏi này. "
        "Câu hỏi cần giảng viên xác nhận thêm."
    )

