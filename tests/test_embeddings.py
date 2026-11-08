import math

import pytest

from app.knowledge.embedding_contract import DIMENSION, PASSAGE_PREFIX, QUERY_PREFIX
from app.knowledge.embeddings import EmbeddingService, validate_vector


@pytest.mark.parametrize(
    "vector",
    [[0.0] * DIMENSION, [1.0], [math.nan] * DIMENSION, [math.inf] * DIMENSION, [1.0] * DIMENSION],
)
def test_invalid_vectors_are_rejected(vector):
    with pytest.raises(ValueError):
        validate_vector(vector)


def test_prefixes_normalization_and_count_without_model_download():
    seen = []

    class Tokenizer:
        def encode(self, text, add_special_tokens=True):
            return text.split()

    class Model:
        def encode(self, texts, **kwargs):
            seen.extend(texts)
            assert kwargs["normalize_embeddings"] is True
            return [[1.0, *([0.0] * (DIMENSION - 1))] for _ in texts]

    service = EmbeddingService()
    service.tokenizer = Tokenizer()
    service.model = Model()

    assert len(service.encode_passages(["Công thức"])[0]) == DIMENSION
    assert len(service.encode_query("Lỗi 401")) == DIMENSION
    assert seen == [PASSAGE_PREFIX + "Công thức", QUERY_PREFIX + "Lỗi 401"]

    with pytest.raises(ValueError, match="limit"):
        service.encode_query("word " * 600)

    with pytest.raises(ValueError, match="empty"):
        service.encode_query(" ")
