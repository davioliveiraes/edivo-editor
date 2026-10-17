"""Allowlisted public inventory; storage keys and private probe output stay internal."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator


class MediaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    kind: Literal["source_video"]
    original_filename: str
    checksum_sha256: str
    byte_size: int
    mime_detected: Literal["video/mp4"]
    duration_ms: int
    width: int
    height: int
    fps_num: int
    fps_den: int
    video_codec: str | None
    has_audio: bool
    audio_codec: str | None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def timestamp_in_utc(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class MediaPage(BaseModel):
    items: list[MediaResponse]
    total: int
    limit: int
    offset: int
