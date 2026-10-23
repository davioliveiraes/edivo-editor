"""R05 durable final exports; historical revisions/media/previews remain untouched."""

import sqlalchemy as sa

from alembic import op

revision = "0005_r05_render_exports"
down_revision = "0004_r04_text_audio_previews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "render_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("edit_spec_revision_id", sa.Uuid(), nullable=False),
        sa.Column("profile", sa.String(length=16), nullable=False),
        sa.Column("render_engine_version", sa.String(length=40), nullable=False),
        sa.Column("idempotency_key", sa.String(length=36), nullable=True),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("claim_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("progress_encoded_ms", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("artifact_storage_key", sa.String(length=200), nullable=True),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=True),
        sa.Column("artifact_byte_size", sa.BigInteger(), nullable=True),
        sa.Column("artifact_duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("artifact_width", sa.Integer(), nullable=True),
        sa.Column("artifact_height", sa.Integer(), nullable=True),
        sa.Column("artifact_video_codec", sa.String(length=16), nullable=True),
        sa.Column("artifact_audio_codec", sa.String(length=16), nullable=True),
        sa.Column("artifact_fps_num", sa.Integer(), nullable=True),
        sa.Column("artifact_fps_den", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "artifact_sha256 IS NULL OR (length(artifact_sha256) = 64 AND "
            "artifact_sha256 = lower(artifact_sha256))",
            name="ck_render_jobs_artifact_checksum",
        ),
        sa.CheckConstraint(
            "artifact_byte_size IS NULL OR artifact_byte_size > 0",
            name="ck_render_jobs_artifact_size",
        ),
        sa.CheckConstraint(
            "max_attempts BETWEEN 1 AND 2 AND attempts >= 0 AND attempts <= max_attempts",
            name="ck_render_jobs_attempts",
        ),
        sa.CheckConstraint(
            "artifact_audio_codec IS NULL OR artifact_audio_codec = 'aac'",
            name="ck_render_jobs_audio_codec",
        ),
        sa.CheckConstraint(
            "status != 'canceled' OR cancel_requested_at IS NOT NULL",
            name="ck_render_jobs_canceled_request",
        ),
        sa.CheckConstraint(
            "status != 'completed' OR (artifact_storage_key IS NOT NULL AND "
            "artifact_sha256 IS NOT NULL AND artifact_byte_size IS NOT NULL AND "
            "artifact_duration_ms IS NOT NULL AND artifact_duration_ms > 0 AND "
            "artifact_width IS NOT NULL AND artifact_width > 0 AND artifact_height IS "
            "NOT NULL AND artifact_height > 0 AND artifact_video_codec IS NOT NULL AND "
            "artifact_video_codec = 'h264' AND artifact_fps_num IS NOT NULL AND "
            "artifact_fps_num = 30 AND artifact_fps_den IS NOT NULL AND artifact_fps_den"
            " = 1 AND cancel_requested_at IS NULL AND error_code IS NULL)",
            name="ck_render_jobs_completed_artifact",
        ),
        sa.CheckConstraint(
            "length(render_engine_version) BETWEEN 1 AND 40",
            name="ck_render_jobs_engine",
        ),
        sa.CheckConstraint(
            "length(request_fingerprint) = 64 AND request_fingerprint = lower(request_fingerprint)",
            name="ck_render_jobs_fingerprint",
        ),
        sa.CheckConstraint(
            "idempotency_key IS NULL OR length(idempotency_key) = 36",
            name="ck_render_jobs_idempotency_key",
        ),
        sa.CheckConstraint(
            "status = 'running' OR (claim_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_render_jobs_inactive_lease",
        ),
        sa.CheckConstraint(
            "profile IN ('standard', 'high')",
            name="ck_render_jobs_profile",
        ),
        sa.CheckConstraint(
            "progress_encoded_ms IS NULL OR progress_encoded_ms >= 0",
            name="ck_render_jobs_progress",
        ),
        sa.CheckConstraint(
            "status != 'running' OR (claim_token IS NOT NULL AND lease_expires_at IS NOT"
            " NULL AND heartbeat_at IS NOT NULL AND attempts >= 1)",
            name="ck_render_jobs_running_lease",
        ),
        sa.CheckConstraint(
            "(status = 'queued' AND stage = 'queued') OR (status = 'running' AND stage "
            "IN ('preparing', 'encoding', 'validating', 'publishing')) OR (status = "
            "'completed' AND stage = 'completed') OR (status = 'failed' AND stage = "
            "'failed') OR (status = 'canceled' AND stage = 'canceled')",
            name="ck_render_jobs_state",
        ),
        sa.CheckConstraint(
            "status NOT IN ('completed', 'failed', 'canceled') OR finished_at IS NOT NULL",
            name="ck_render_jobs_terminal_time",
        ),
        sa.CheckConstraint(
            "status = 'completed' OR (artifact_storage_key IS NULL AND artifact_sha256 "
            "IS NULL AND artifact_byte_size IS NULL AND artifact_duration_ms IS NULL AND"
            " artifact_width IS NULL AND artifact_height IS NULL AND "
            "artifact_video_codec IS NULL AND artifact_audio_codec IS NULL AND "
            "artifact_fps_num IS NULL AND artifact_fps_den IS NULL)",
            name="ck_render_jobs_unpublished_artifact",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["edit_spec_revision_id"], ["edit_spec_revisions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("artifact_storage_key"),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_render_jobs_project_key"),
    )
    op.create_index(
        "ix_render_jobs_edit_spec_revision_id", "render_jobs", ["edit_spec_revision_id"]
    )
    op.create_index(
        "ix_render_jobs_equivalent", "render_jobs", ["project_id", "request_fingerprint", "status"]
    )
    op.create_index(
        "ix_render_jobs_history",
        "render_jobs",
        ["project_id", sa.text("created_at DESC"), sa.text("id DESC")],
    )
    op.create_index("ix_render_jobs_lease", "render_jobs", ["status", "lease_expires_at"])
    op.create_index("ix_render_jobs_project_id", "render_jobs", ["project_id"])
    op.create_index("ix_render_jobs_queue", "render_jobs", ["status", "created_at", "id"])
    op.create_index(
        "uq_render_jobs_active_project",
        "render_jobs",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
        sqlite_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    raise RuntimeError("R05 downgrade requires a separately reviewed preservation plan.")
