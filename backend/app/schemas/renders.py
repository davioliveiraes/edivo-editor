"""Final-export requests contain saved revision identity, never executable plans."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

RenderProfile = Literal["standard", "high"]
RenderStatus = Literal["queued", "running", "completed", "failed", "canceled"]


class RenderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edit_spec_revision_id: UUID
    profile: RenderProfile
    idempotency_key: UUID | None = None


class RenderResponse(BaseModel):
    id: UUID
    project_id: UUID
    revision_id: UUID
    revision_number: int
    profile: RenderProfile
    preset: Literal["9:16", "1:1", "4:5", "16:9"]
    render_engine_version: str
    status: RenderStatus
    stage: Literal[
        "queued",
        "preparing",
        "encoding",
        "validating",
        "publishing",
        "completed",
        "failed",
        "canceled",
    ]
    attempts: int
    max_attempts: int
    progress_encoded_ms: int | None
    progress_percent: int | None
    cancel_requested_at: datetime | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    artifact_available: bool
    checksum_sha256: str | None
    byte_size: int | None
    duration_ms: int | None
    width: int | None
    height: int | None
    fps_num: int | None
    fps_den: int | None
    video_codec: str | None
    audio_codec: str | None
    status_url: str
    file_url: str | None

    @field_validator("cancel_requested_at", "created_at", "updated_at", "started_at", "finished_at")
    @classmethod
    def timestamp_in_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class RenderPage(BaseModel):
    items: list[RenderResponse]
    total: int
    limit: int
    offset: int
