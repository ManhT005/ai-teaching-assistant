from app.context.contracts import ContextTurn, ResolutionStatus, TeachingIntent
from app.context.rewriter import resolve_query


def test_independent_question_ignores_context():
    result = resolve_query(8, "HTTP 403 là gì?", (ContextTurn(7, "n8n HTTP 401 Unauthorized"),))
    assert result.status == ResolutionStatus.STANDALONE
    assert result.antecedent_event_ids == ()
    assert result.standalone_question == "HTTP 403 là gì?"


def test_followup_reference_preserves_new_status_and_uses_topic_only():
    result = resolve_query(8, "Còn 403?", (ContextTurn(7, "n8n HTTP 401 Unauthorized"),))
    assert result.status == ResolutionStatus.RESOLVED
    assert result.intent == TeachingIntent.FOLLOWUP_REFERENCE
    assert result.antecedent_event_ids == (7,)
    assert "403" in result.standalone_question
    assert "401" not in result.standalone_question
    assert "n8n" in result.standalone_question


def test_elliptical_question_without_history_fails_closed():
    result = resolve_query(8, "Cho em ví dụ?", ())
    assert result.status == ResolutionStatus.NO_CONTEXT


def test_generic_reference_with_multiple_turns_is_ambiguous():
    turns = (ContextTurn(6, "SUMIFS trong bảng tính"), ContextTurn(7, "n8n HTTP request"))
    result = resolve_query(8, "Cái đó thì sao?", turns)
    assert result.status == ResolutionStatus.AMBIGUOUS


def test_explicit_new_topic_clears_history():
    result = resolve_query(
        8, "Chuyển sang chủ đề khác: SUMIFS là gì?", (ContextTurn(7, "n8n HTTP"),)
    )
    assert result.status == ResolutionStatus.STANDALONE
    assert result.intent == TeachingIntent.NEW_TOPIC
    assert result.antecedent_event_ids == ()
