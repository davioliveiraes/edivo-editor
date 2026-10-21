"""Strict preview requests reference immutable revisions, never a mutable draft."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PreviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edit_spec_revision_id: UUID
    preview_start_ms: Annotated[int, Field(strict=True, ge=0)]
    duration_ms: Annotated[int, Field(strict=True, ge=1, le=8000)]


class PreviewResponse(BaseModel):
    id: UUID
    project_id: UUID
    revision_id: UUID
    revision_number: int
    preview_start_ms: int
    duration_ms: int
    status: Literal["queued", "running", "completed", "failed"]
    stage: Literal[
        "queued", "preparing", "encoding", "validating", "publishing", "completed", "failed"
    ]
    attempts: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    artifact_available: bool
    checksum_sha256: str | None
    byte_size: int | None
    status_url: str
    file_url: str | None

    @field_validator("created_at", "started_at", "finished_at")
    @classmethod
    def timestamp_in_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
