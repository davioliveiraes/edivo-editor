"""Versioned editing plans contain validated data, never executable render options."""

import re
import unicodedata
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

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


class _PlanBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_media_id: UUID
    trim: TrimSpec
    canvas: CanvasSpec

    @model_validator(mode="before")
    @classmethod
    def strict_schema_version(cls, value: object) -> object:
        # Literal alone compares True/1.0 equal to 1; the wire contract is an integer.
        if isinstance(value, dict) and type(value.get("schema_version")) is not int:
            raise ValueError("A versão deve ser um inteiro.")
        return value


class EditSpecV1(_PlanBase):
    schema_version: Literal[1]


class TextOverlay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    text: Annotated[str, Field(strict=True)]
    start_ms: Milliseconds
    end_ms: Milliseconds
    position: Literal["top", "center", "bottom"]
    style: Literal["white_outline", "blue_outline", "white_plain"]
    size: Literal["small", "medium", "large"]

    @field_validator("text")
    @classmethod
    def safe_plain_text(cls, value: str) -> str:
        # Check before strip so invisible controls at either end are not discarded silently.
        if any(
            (unicodedata.category(c).startswith("C") and c != "\n")
            or unicodedata.category(c) in {"Zl", "Zp"}
            for c in value
        ):
            raise ValueError("O texto contém caracteres de controle.")
        value = value.strip()
        if not 1 <= len(value) <= 120 or value.count("\n") > 1:
            raise ValueError("Use de 1 a 120 caracteres e até duas linhas.")
        if re.search(r"<[^>]*>|%\{[^}]*\}", value):
            raise ValueError("Use somente texto simples, sem marcação ou expressões.")
        return value

    @model_validator(mode="after")
    def ordered_interval(self) -> "TextOverlay":
        if self.end_ms <= self.start_ms:
            raise ValueError("O fim do texto deve ser posterior ao início.")
        return self


class AudioSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_volume: Annotated[float, Field(strict=True, ge=0, le=2, allow_inf_nan=False)] = 1.0
    background_audio_id: UUID | None = None
    background_volume: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)] = 0.15
    background_loop: Annotated[bool, Field(strict=True)] = False
    fade_in_ms: Annotated[int, Field(strict=True, ge=0, le=3000)] = 0
    fade_out_ms: Annotated[int, Field(strict=True, ge=0, le=3000)] = 0


class EditSpecV2(_PlanBase):
    schema_version: Literal[2]
    text_overlays: Annotated[list[TextOverlay], Field(max_length=3)] = Field(default_factory=list)
    audio: AudioSpec = Field(default_factory=AudioSpec)

    @model_validator(mode="after")
    def intervals_within_trim(self) -> "EditSpecV2":
        identifiers = [overlay.id for overlay in self.text_overlays]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Os identificadores dos textos devem ser distintos.")
        if any(
            overlay.start_ms < self.trim.start_ms or overlay.end_ms > self.trim.end_ms
            for overlay in self.text_overlays
        ):
            raise ValueError("Os textos devem permanecer dentro do trecho.")
        if self.audio.fade_in_ms + self.audio.fade_out_ms > self.trim.end_ms - self.trim.start_ms:
            raise ValueError("A soma dos fades excede a duração do trecho.")
        return self


EditSpec = Annotated[EditSpecV1 | EditSpecV2, Field(discriminator="schema_version")]


class EditSpecV1Put(EditSpecV1):
    expected_revision: Annotated[int, Field(strict=True, ge=0)]

    def plan(self) -> EditSpecV1:
        return EditSpecV1.model_validate(self.model_dump(exclude={"expected_revision"}))


class EditSpecV2Put(EditSpecV2):
    expected_revision: Annotated[int, Field(strict=True, ge=0)]

    def plan(self) -> EditSpecV2:
        return EditSpecV2.model_validate(self.model_dump(exclude={"expected_revision"}))


EditSpecPut = Annotated[EditSpecV1Put | EditSpecV2Put, Field(discriminator="schema_version")]


class _RevisionMetadata(BaseModel):
    id: UUID
    project_id: UUID
    revision_number: int
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def timestamp_in_utc(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class EditSpecV1Response(EditSpecV1, _RevisionMetadata):
    pass


class EditSpecV2Response(EditSpecV2, _RevisionMetadata):
    pass


EditSpecResponse = Annotated[
    EditSpecV1Response | EditSpecV2Response, Field(discriminator="schema_version")
]
edit_spec_response_adapter = TypeAdapter(EditSpecResponse)
