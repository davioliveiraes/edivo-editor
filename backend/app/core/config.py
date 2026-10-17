"""Validated runtime configuration; secrets are never represented as plain strings."""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EDIVO_", env_file=("../.env", ".env"), extra="ignore", hide_input_in_errors=True
    )

    env: Literal["development", "test"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: SecretStr
    database_connect_timeout: int = Field(default=2, ge=1, le=10)
    storage_root: Path = Field(default=Path("../storage"), repr=False)
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://127.0.0.1:5173", "http://localhost:5173"]
    )
    ffmpeg_bin: str = Field(default="ffmpeg", min_length=1, repr=False)
    ffprobe_bin: str = Field(default="ffprobe", min_length=1, repr=False)
    ffmpeg_timeout_seconds: float = Field(default=5, gt=0, le=30)
    capabilities_cache_seconds: float = Field(default=300, ge=1, le=3600)
    upload_max_bytes: int = Field(default=500 * 1024 * 1024, ge=1, le=2 * 1024**3)
    upload_multipart_overhead_bytes: int = Field(default=64 * 1024, ge=1024, le=1024 * 1024)
    media_max_duration_seconds: int = Field(default=600, ge=1, le=86400)
    ffprobe_timeout_seconds: float = Field(default=15, gt=0, le=120)
    ffprobe_max_output_bytes: int = Field(default=1024 * 1024, ge=1024, le=8 * 1024 * 1024)
    storage_min_free_bytes: int = Field(default=64 * 1024 * 1024, ge=0, le=10 * 1024**3)

    @field_validator("database_url")
    @classmethod
    def postgres_psycopg_only(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
        except Exception:
            raise ValueError("EDIVO_DATABASE_URL deve ser uma URL PostgreSQL válida.") from None
        if url.drivername != "postgresql+psycopg" or not all(
            [url.host, url.database, url.username, url.password]
        ):
            raise ValueError("EDIVO_DATABASE_URL requer PostgreSQL, psycopg e credenciais.")
        if url.query:
            raise ValueError("Configure timeouts pelas variáveis EDIVO_; URL sem query exigida.")
        return value

    @field_validator("cors_origins")
    @classmethod
    def local_origins_only(cls, origins: list[str]) -> list[str]:
        for origin in origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("CORS deve conter somente origens locais completas.")
        return origins


@lru_cache
def get_settings() -> Settings:
    return Settings()
