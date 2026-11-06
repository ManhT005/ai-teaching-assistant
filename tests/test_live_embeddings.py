"""Explicit local acceptance; CI never downloads weights or claims semantic quality."""

import os

import pytest
from test_ingestion_db import section

from app.chunking import chunk_sections
from app.embeddings import EmbeddingService, validate_vector


def test_live_e5_vietnamese_vectors_and_exact_tokenizer_budget():
    if os.getenv("RUN_LIVE_EMBEDDING") != "1":
        pytest.skip("Set RUN_LIVE_EMBEDDING=1 after downloading the pinned E5 model")

    service = EmbeddingService()
    assert service.settings.embedding_local_files_only is True
    vectors = service.encode_passages(
        ["Công thức SUMIFS theo ngày", "Em chạy n8n bị lỗi 401 Unauthorized"]
    )
    assert len(vectors) == 2
    assert all(len(validate_vector(vector)) == 384 for vector in vectors)
    assert len(service.encode_query("Lỗi 401 trong n8n")) == 384

    long_source = section("GS-02 Công thức\n" + "Tiếng Việt có dấu =SUMIFS(A:A;B:B;1). " * 100)
    chunks = chunk_sections([long_source], service.tokenizer)
    assert len(chunks) > 1
    assert all(chunk.token_count <= 320 for chunk in chunks)

    with pytest.raises(ValueError, match="limit"):
        service.encode_query("Tiếng Việt " * 1000)
