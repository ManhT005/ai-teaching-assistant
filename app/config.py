"""Environment-based configuration. No credentials in source control."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str | None = None
    mock_webhook_token: str | None = None
    log_level: str = "INFO"
    embedding_cache_dir: Path = Path(".models")
    embedding_local_files_only: bool = True
    embedding_batch_size: int = Field(default=16, ge=1, le=128)
    chunk_target_tokens: int = Field(default=320, ge=64, le=480)
    chunk_overlap_tokens: int = Field(default=40, ge=0, le=64)


@lru_cache
def get_settings() -> Settings:
    return Settings()
