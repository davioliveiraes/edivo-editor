"""The project API accepts only the small R02 metadata contract."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TargetPreset = Literal["9:16", "1:1", "4:5", "16:9"]


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    purpose: str | None = Field(default=None, max_length=500)
    target_preset: TargetPreset | None = None


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=120)
    purpose: str | None = Field(default=None, max_length=500)
    target_preset: TargetPreset | None = None

    @model_validator(mode="after")
    def require_change(self) -> "ProjectUpdate":
        if not self.model_fields_set or ("name" in self.model_fields_set and self.name is None):
            raise ValueError("Informe ao menos um campo válido para atualizar.")
        return self


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    purpose: str | None
    target_preset: TargetPreset | None
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def timestamps_in_utc(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class ProjectPage(BaseModel):
    items: list[ProjectResponse]
    total: int
    limit: int
    offset: int
