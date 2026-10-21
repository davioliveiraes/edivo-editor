"""MP3 originals are separate from the immutable R02 source video inventory."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    desc,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.project import utc_now


class AudioAsset(Base):
    __tablename__ = "audio_assets"
    __table_args__ = (
        CheckConstraint("byte_size > 0", name="ck_audio_assets_byte_size"),
        CheckConstraint(
            "duration_ms > 0 AND duration_ms <= 600000", name="ck_audio_assets_duration"
        ),
        CheckConstraint(
            "sample_rate > 0 AND sample_rate <= 192000", name="ck_audio_assets_sample_rate"
        ),
        CheckConstraint("channels > 0 AND channels <= 2", name="ck_audio_assets_channels"),
        CheckConstraint("mime_detected = 'audio/mpeg'", name="ck_audio_assets_mime"),
        CheckConstraint("codec = 'mp3'", name="ck_audio_assets_codec"),
        CheckConstraint(
            "length(checksum_sha256) = 64 AND checksum_sha256 = lower(checksum_sha256)",
            name="ck_audio_assets_checksum",
        ),
        Index("ix_audio_assets_project_created_id", "project_id", desc("created_at"), desc("id")),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    original_filename: Mapped[str] = mapped_column(String(160), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_detected: Mapped[str] = mapped_column(String(40), nullable=False)
    duration_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    codec: Mapped[str] = mapped_column(String(64), nullable=False)
    sample_rate: Mapped[int] = mapped_column(Integer, nullable=False)
    channels: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
