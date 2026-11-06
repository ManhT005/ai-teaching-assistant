from app.chunking import chunk_sections
from app.ingestion import KnowledgeChunk, SourceBlock


class CharacterTokenizer:
    """Conservative deterministic tokenizer for offline boundary tests only."""

    def encode(self, text, add_special_tokens=True):
        return list(text) + ([0, 0] if add_special_tokens else [])


def section(content, blocks=()):
    return KnowledgeChunk(
        record_id="GS-02",
        doc_id="DOC-GS-01",
        section_id="GS-02",
        version="TEST-1.0",
        class_scope="GS-KPI-01",
        content=content,
        source_name="course.docx",
        locator="block 1",
        data_origin="provided_sample",
        review_status="provided_sample",
        source_hash="a" * 64,
        blocks=blocks,
    )


def test_long_vietnamese_and_formula_keep_all_text_with_stable_ids():
    formula = '=SUMIFS(D2:D;A2:A;">="&F2;A2:A;"<"&G2+1)'
    body = "Công thức tiếng Việt " * 80 + formula
    source = section("GS-02 Tổng\n" + body)
    chunks = chunk_sections([source], CharacterTokenizer(), target_tokens=100, overlap_tokens=0)

    assert len(chunks) > 1
    assert "".join(chunk.content.split("\n", 1)[1] for chunk in chunks) == body
    assert all(chunk.token_count <= 100 and chunk.locator and chunk.source_hash for chunk in chunks)
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert chunks == chunk_sections(
        [source], CharacterTokenizer(), target_tokens=100, overlap_tokens=0
    )


def test_short_table_formula_is_atomic_and_locator_matches_group():
    blocks = (
        SourceBlock(content="GS-02 Tổng", locator="block 1"),
        SourceBlock(content="A" * 55, locator="block 2"),
        SourceBlock(content="=SUMIFS(A:A;B:B;1) | 401", locator="block 3 / table 1 / row 2"),
    )
    chunks = chunk_sections(
        [section("\n".join(b.content for b in blocks), blocks)], CharacterTokenizer(), 90, 0
    )

    assert len(chunks) == 2
    assert "=SUMIFS(A:A;B:B;1) | 401" in chunks[1].content
    assert "table 1 / row 2" in chunks[1].locator
    assert "block 2" not in chunks[1].locator


def test_sections_never_share_a_chunk_and_heading_always_repeats():
    first = section("GS-02 Tổng\n" + "a " * 200)
    second = first.model_copy(update={"section_id": "GS-04", "content": "GS-04 Nhập\nKhác"})
    chunks = chunk_sections([first, second], CharacterTokenizer(), 100, 20)

    assert all(chunk.content.startswith(chunk.section_id) for chunk in chunks)
    assert len({chunk.chunk_id for chunk in chunks}) == len(chunks)
    assert chunks[-1].section_id == "GS-04"
