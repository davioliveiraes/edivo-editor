"""Durable, fenced preview state; processing is owned by the independent worker."""

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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.project import utc_now


class PreviewJob(Base):
    __tablename__ = "preview_jobs"
    __table_args__ = (
        CheckConstraint("preview_start_ms >= 0", name="ck_preview_jobs_start"),
        CheckConstraint(
            "duration_ms >= 1 AND duration_ms <= 8000", name="ck_preview_jobs_duration"
        ),
        CheckConstraint("profile_version >= 1", name="ck_preview_jobs_profile"),
        CheckConstraint("length(idempotency_key) = 64", name="ck_preview_jobs_idempotency"),
        CheckConstraint("attempts >= 0 AND attempts <= 2", name="ck_preview_jobs_attempts"),
        CheckConstraint(
            "(status = 'queued' AND stage = 'queued') OR "
            "(status = 'running' AND stage IN "
            "('preparing', 'encoding', 'validating', 'publishing')) OR "
            "(status = 'completed' AND stage = 'completed') OR "
            "(status = 'failed' AND stage = 'failed')",
            name="ck_preview_jobs_state",
        ),
        CheckConstraint(
            "status != 'running' OR (claim_token IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND heartbeat_at IS NOT NULL AND attempts >= 1)",
            name="ck_preview_jobs_running_lease",
        ),
        CheckConstraint(
            "artifact_byte_size IS NULL OR artifact_byte_size > 0",
            name="ck_preview_jobs_artifact_size",
        ),
        CheckConstraint(
            "artifact_sha256 IS NULL OR (length(artifact_sha256) = 64 "
            "AND artifact_sha256 = lower(artifact_sha256))",
            name="ck_preview_jobs_artifact_checksum",
        ),
        CheckConstraint(
            "status != 'completed' OR (artifact_storage_key IS NOT NULL "
            "AND artifact_sha256 IS NOT NULL AND artifact_byte_size IS NOT NULL "
            "AND finished_at IS NOT NULL)",
            name="ck_preview_jobs_completed_artifact",
        ),
        Index("ix_preview_jobs_status_created_id", "status", "created_at", "id"),
        Index("ix_preview_jobs_lease", "status", "lease_expires_at"),
        Index(
            "uq_preview_jobs_live_request",
            "project_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running', 'completed')"),
            sqlite_where=text("status IN ('queued', 'running', 'completed')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    edit_spec_revision_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("edit_spec_revisions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    preview_start_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    profile_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    stage: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    claim_token: Mapped[UUID | None] = mapped_column(Uuid)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    artifact_storage_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64))
    artifact_byte_size: Mapped[int | None] = mapped_column(BigInteger)
    error_code: Mapped[str | None] = mapped_column(String(64))
