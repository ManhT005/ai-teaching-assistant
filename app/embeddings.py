"""Pinned multilingual E5 on CPU. No fallback and no silent truncation."""

import argparse
import json
import math
from functools import cached_property

from app.config import Settings, get_settings
from app.embedding_contract import (
    DIMENSION, MAX_INPUT_TOKENS, MODEL_ID, MODEL_KEY, MODEL_REVISION,
    PASSAGE_PREFIX, QUERY_PREFIX,
)


def validate_vector(vector, dimension: int = DIMENSION) -> list[float]:
    values = [float(value) for value in vector]

    if len(values) != dimension or not all(math.isfinite(value) for value in values):
        raise ValueError("Invalid embedding dimension or non-finite values")

    norm = math.sqrt(sum(value * value for value in values))

    if not math.isfinite(norm) or norm <= 0:
        raise ValueError("Embedding must have a finite, nonzero norm")

    if not math.isclose(norm, 1.0, abs_tol=1e-4):
        raise ValueError("Embedding must be normalized")

    return values


class EmbeddingService:
    model_id = MODEL_KEY
    dimension = DIMENSION

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    @cached_property
    def tokenizer(self):
        from transformers import AutoTokenizer

        try:
            return AutoTokenizer.from_pretrained(
                MODEL_ID,
                revision=MODEL_REVISION,
                cache_dir=str(self.settings.embedding_cache_dir),
                local_files_only=self.settings.embedding_local_files_only,
                trust_remote_code=False,
            )
        except Exception:
            raise RuntimeError("E5 tokenizer unavailable; run python -m app.embeddings --download") from None

    @cached_property
    def model(self):
        from sentence_transformers import SentenceTransformer

        try:
            model = SentenceTransformer(
                MODEL_ID,
                revision=MODEL_REVISION,
                device="cpu",
                cache_folder=str(self.settings.embedding_cache_dir),
                local_files_only=self.settings.embedding_local_files_only,
                trust_remote_code=False,
            )
        except Exception:
            raise RuntimeError("E5 weights unavailable; run python -m app.embeddings --download") from None

        if model.get_sentence_embedding_dimension() != DIMENSION or model.max_seq_length != MAX_INPUT_TOKENS:
            raise ValueError("Model does not match the fixed E5 embedding contract")

        return model

    def _encode(self, texts: list[str], prefix: str) -> list[list[float]]:
        if not texts:
            return []

        prefixed = [prefix + text for text in texts]

        if any(not text.strip() for text in texts):
            raise ValueError("Cannot embed empty text")

        if any(len(self.tokenizer.encode(text, add_special_tokens=True)) > MAX_INPUT_TOKENS for text in prefixed):
            raise ValueError("Embedding input exceeds the model token limit")

        vectors = self.model.encode(
            prefixed,
            batch_size=self.settings.embedding_batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )

        if len(vectors) != len(texts):
            raise ValueError("Embedding count differs from input count")

        return [validate_vector(vector) for vector in vectors]

    def encode_passages(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts, PASSAGE_PREFIX)

    def encode_query(self, text: str) -> list[float]:
        return self._encode([text], QUERY_PREFIX)[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="Allow downloading the pinned model into the local cache")
    args = parser.parse_args()
    settings = get_settings().model_copy(update={"embedding_local_files_only": not args.download})
    service = EmbeddingService(settings)
    vectors = service.encode_passages(["Công thức SUMIFS theo khoảng ngày", "Lỗi 401 khi gọi API trong n8n"])

    print(json.dumps({"model": service.model_id, "dimension": service.dimension, "vectors": len(vectors), "device": "cpu"}))


if __name__ == "__main__":
    main()
