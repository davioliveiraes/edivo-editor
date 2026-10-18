"""The complete R03 plan is data, with no render flags or transient player state."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.projects import TargetPreset

Milliseconds = Annotated[int, Field(strict=True, ge=0)]
FocalCoordinate = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]


class TrimSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_ms: Milliseconds
    end_ms: Milliseconds

    @model_validator(mode="after")
    def ordered_interval(self) -> "TrimSpec":
        if self.end_ms <= self.start_ms:
            raise ValueError("O fim deve ser posterior ao início.")
        return self


class CanvasSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset: TargetPreset
    fit: Literal["cover", "contain"]
    focal_x: FocalCoordinate
    focal_y: FocalCoordinate


class EditSpecV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    source_media_id: UUID
    trim: TrimSpec
    canvas: CanvasSpec


class EditSpecPut(EditSpecV1):
    expected_revision: Annotated[int, Field(strict=True, ge=0)]

    def plan(self) -> EditSpecV1:
        return EditSpecV1.model_validate(self.model_dump(exclude={"expected_revision"}))


class EditSpecResponse(EditSpecV1):
    id: UUID
    project_id: UUID
    revision_number: int
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def timestamp_in_utc(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
