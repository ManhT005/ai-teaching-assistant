"""Environment-based configuration. No credentials in source control."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str | None = None
    mock_webhook_token: str | None = None
    operator_api_token: str | None = None
    event_max_attempts: int = Field(default=3, ge=1, le=10)
    event_lease_seconds: int = Field(default=120, ge=10, le=900)
    event_backoff_base_seconds: int = Field(default=2, ge=1, le=60)
    event_backoff_cap_seconds: int = Field(default=60, ge=1, le=3600)
    event_poll_interval_seconds: float = Field(default=1.0, gt=0, le=60)
    delivery_max_attempts: int = Field(default=3, ge=1, le=10)
    delivery_lease_seconds: int = Field(default=60, ge=10, le=900)
    webhook_sender_limit: int = Field(default=10, ge=1, le=1000)
    webhook_group_limit: int = Field(default=100, ge=1, le=10000)
    webhook_rate_window_seconds: int = Field(default=60, ge=1, le=3600)
    log_level: str = "INFO"
    embedding_cache_dir: Path = Path(".models")
    embedding_local_files_only: bool = True
    embedding_batch_size: int = Field(default=16, ge=1, le=128)
    chunk_target_tokens: int = Field(default=320, ge=64, le=480)
    chunk_overlap_tokens: int = Field(default=40, ge=0, le=64)
    retrieval_dense_candidates: int = Field(default=10, ge=5, le=50)
    retrieval_lexical_candidates: int = Field(default=10, ge=5, le=50)
    retrieval_max_lexical_terms: int = Field(default=6, ge=1, le=12)
    retrieval_rrf_k: int = Field(default=60, ge=1, le=1000)
    llm_provider: Literal["fake", "gemini"] = "fake"
    gemini_api_key: SecretStr | None = None
    gemini_model: str = Field(default="", pattern=r"^[a-zA-Z0-9._-]*$")
    llm_timeout_seconds: float = Field(default=20, gt=0, le=120)
    llm_max_output_tokens: int = Field(default=2048, ge=256, le=4096)
    llm_max_context_characters: int = Field(default=12000, ge=1000, le=24000)


@lru_cache
def get_settings() -> Settings:
    return Settings()
