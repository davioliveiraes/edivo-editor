"""Original media bytes live in private storage; this entity contains their inventory."""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    desc,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.project import utc_now

if TYPE_CHECKING:
    from app.models.project import Project


class MediaAsset(Base):
    __tablename__ = "media_assets"
    __table_args__ = (
        CheckConstraint("kind = 'source_video'", name="ck_media_assets_kind"),
        CheckConstraint("byte_size > 0", name="ck_media_assets_byte_size"),
        CheckConstraint("duration_ms > 0", name="ck_media_assets_duration_ms"),
        CheckConstraint("width > 0 AND height > 0", name="ck_media_assets_dimensions"),
        CheckConstraint("fps_num > 0 AND fps_den > 0", name="ck_media_assets_fps"),
        CheckConstraint(
            "length(checksum_sha256) = 64 AND checksum_sha256 = lower(checksum_sha256)",
            name="ck_media_assets_checksum",
        ),
        CheckConstraint("mime_detected = 'video/mp4'", name="ck_media_assets_mime"),
        CheckConstraint("has_audio OR audio_codec IS NULL", name="ck_media_assets_audio"),
        Index("ix_media_assets_project_created_id", "project_id", desc("created_at"), desc("id")),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(20), default="source_video", nullable=False)
    original_filename: Mapped[str] = mapped_column(String(160), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_detected: Mapped[str] = mapped_column(String(40), nullable=False)
    duration_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    fps_num: Mapped[int] = mapped_column(Integer, nullable=False)
    fps_den: Mapped[int] = mapped_column(Integer, nullable=False)
    video_codec: Mapped[str | None] = mapped_column(String(64))
    has_audio: Mapped[bool] = mapped_column(Boolean, nullable=False)
    audio_codec: Mapped[str | None] = mapped_column(String(64))
    probe_metadata: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    project: Mapped["Project"] = relationship(back_populates="media_assets")
