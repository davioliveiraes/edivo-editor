"""Public MP3 inventory intentionally excludes internal storage keys."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator


class AudioResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    original_filename: str
    checksum_sha256: str
    byte_size: int
    mime_detected: Literal["audio/mpeg"]
    duration_ms: int
    codec: Literal["mp3"]
    sample_rate: int
    channels: int
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def timestamp_in_utc(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class AudioPage(BaseModel):
    items: list[AudioResponse]
    total: int
    limit: int
    offset: int
