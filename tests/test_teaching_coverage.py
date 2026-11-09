from app.context.contracts import TeachingIntent
from app.teaching.coverage import ClaimCoverage, assess_claim_coverage
from app.teaching.formatter import format_teaching_reply


def test_claim_coverage_requires_exact_source_span():
    evidence = {"chunk-a": ("The exact documented paragraph.",)}
    assert (
        assess_claim_coverage(("The exact documented paragraph.",), evidence)
        == ClaimCoverage.SUPPORTED
    )
    assert (
        assess_claim_coverage(
            ("The exact documented paragraph.", "An inferred sentence."), evidence
        )
        == ClaimCoverage.PARTIALLY_SUPPORTED
    )
    assert assess_claim_coverage(("An inferred sentence.",), evidence) == ClaimCoverage.UNSUPPORTED


def test_formatter_uses_server_heading_and_verbatim_content():
    quote = "A complete source paragraph."
    result = format_teaching_reply(TeachingIntent.FOLLOWUP_EXAMPLE, quote)
    assert result == "Ví dụ từ tài liệu\n" + quote
