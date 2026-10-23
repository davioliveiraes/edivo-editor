"""Persistent final exports; immutable source revisions are never rewritten."""

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
    UniqueConstraint,
    Uuid,
    desc,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.project import utc_now


class RenderJob(Base):
    __tablename__ = "render_jobs"
    __table_args__ = (
        CheckConstraint("profile IN ('standard', 'high')", name="ck_render_jobs_profile"),
        CheckConstraint(
            "length(render_engine_version) BETWEEN 1 AND 40", name="ck_render_jobs_engine"
        ),
        CheckConstraint(
            "idempotency_key IS NULL OR length(idempotency_key) = 36",
            name="ck_render_jobs_idempotency_key",
        ),
        CheckConstraint(
            "length(request_fingerprint) = 64 AND request_fingerprint = lower(request_fingerprint)",
            name="ck_render_jobs_fingerprint",
        ),
        CheckConstraint(
            "max_attempts BETWEEN 1 AND 2 AND attempts >= 0 AND attempts <= max_attempts",
            name="ck_render_jobs_attempts",
        ),
        CheckConstraint(
            "(status = 'queued' AND stage = 'queued') OR "
            "(status = 'running' AND stage IN "
            "('preparing', 'encoding', 'validating', 'publishing')) OR "
            "(status = 'completed' AND stage = 'completed') OR "
            "(status = 'failed' AND stage = 'failed') OR "
            "(status = 'canceled' AND stage = 'canceled')",
            name="ck_render_jobs_state",
        ),
        CheckConstraint(
            "status != 'running' OR (claim_token IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND heartbeat_at IS NOT NULL AND attempts >= 1)",
            name="ck_render_jobs_running_lease",
        ),
        CheckConstraint(
            "status = 'running' OR (claim_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_render_jobs_inactive_lease",
        ),
        CheckConstraint(
            "progress_encoded_ms IS NULL OR progress_encoded_ms >= 0",
            name="ck_render_jobs_progress",
        ),
        CheckConstraint(
            "status NOT IN ('completed', 'failed', 'canceled') OR finished_at IS NOT NULL",
            name="ck_render_jobs_terminal_time",
        ),
        CheckConstraint(
            "status != 'canceled' OR cancel_requested_at IS NOT NULL",
            name="ck_render_jobs_canceled_request",
        ),
        CheckConstraint(
            "artifact_byte_size IS NULL OR artifact_byte_size > 0",
            name="ck_render_jobs_artifact_size",
        ),
        CheckConstraint(
            "artifact_sha256 IS NULL OR (length(artifact_sha256) = 64 "
            "AND artifact_sha256 = lower(artifact_sha256))",
            name="ck_render_jobs_artifact_checksum",
        ),
        CheckConstraint(
            "status != 'completed' OR (artifact_storage_key IS NOT NULL "
            "AND artifact_sha256 IS NOT NULL AND artifact_byte_size IS NOT NULL "
            "AND artifact_duration_ms IS NOT NULL AND artifact_duration_ms > 0 "
            "AND artifact_width IS NOT NULL AND artifact_width > 0 "
            "AND artifact_height IS NOT NULL AND artifact_height > 0 "
            "AND artifact_video_codec IS NOT NULL AND artifact_video_codec = 'h264' "
            "AND artifact_fps_num IS NOT NULL AND artifact_fps_num = 30 "
            "AND artifact_fps_den IS NOT NULL AND artifact_fps_den = 1 "
            "AND cancel_requested_at IS NULL AND error_code IS NULL)",
            name="ck_render_jobs_completed_artifact",
        ),
        CheckConstraint(
            "status = 'completed' OR (artifact_storage_key IS NULL AND artifact_sha256 IS NULL "
            "AND artifact_byte_size IS NULL AND artifact_duration_ms IS NULL "
            "AND artifact_width IS NULL AND artifact_height IS NULL "
            "AND artifact_video_codec IS NULL AND artifact_audio_codec IS NULL "
            "AND artifact_fps_num IS NULL AND artifact_fps_den IS NULL)",
            name="ck_render_jobs_unpublished_artifact",
        ),
        CheckConstraint(
            "artifact_audio_codec IS NULL OR artifact_audio_codec = 'aac'",
            name="ck_render_jobs_audio_codec",
        ),
        UniqueConstraint("project_id", "idempotency_key", name="uq_render_jobs_project_key"),
        Index("ix_render_jobs_queue", "status", "created_at", "id"),
        Index("ix_render_jobs_lease", "status", "lease_expires_at"),
        Index("ix_render_jobs_history", "project_id", desc("created_at"), desc("id")),
        Index("ix_render_jobs_equivalent", "project_id", "request_fingerprint", "status"),
        Index(
            "uq_render_jobs_active_project",
            "project_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
            sqlite_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    edit_spec_revision_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("edit_spec_revisions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    profile: Mapped[str] = mapped_column(String(16), nullable=False)
    render_engine_version: Mapped[str] = mapped_column(String(40), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(36))
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    stage: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    claim_token: Mapped[UUID | None] = mapped_column(Uuid)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    progress_encoded_ms: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    artifact_storage_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64))
    artifact_byte_size: Mapped[int | None] = mapped_column(BigInteger)
    artifact_duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    artifact_width: Mapped[int | None] = mapped_column(Integer)
    artifact_height: Mapped[int | None] = mapped_column(Integer)
    artifact_video_codec: Mapped[str | None] = mapped_column(String(16))
    artifact_audio_codec: Mapped[str | None] = mapped_column(String(16))
    artifact_fps_num: Mapped[int | None] = mapped_column(Integer)
    artifact_fps_den: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(64))
