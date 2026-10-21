"""R04 MP3 inventory and preview jobs; v1 snapshots and append-only trigger stay intact."""

import sqlalchemy as sa

from alembic import op

revision = "0004_r04_text_audio_previews"
down_revision = "0003_r03_edit_spec_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_edit_spec_revisions_schema_version", "edit_spec_revisions", type_="check"
    )
    op.create_check_constraint(
        "ck_edit_spec_revisions_schema_version", "edit_spec_revisions", "schema_version IN (1, 2)"
    )
    op.create_table(
        "audio_assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("original_filename", sa.String(160), nullable=False),
        sa.Column("storage_key", sa.String(200), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("mime_detected", sa.String(40), nullable=False),
        sa.Column("duration_ms", sa.BigInteger(), nullable=False),
        sa.Column("codec", sa.String(64), nullable=False),
        sa.Column("sample_rate", sa.Integer(), nullable=False),
        sa.Column("channels", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("byte_size > 0", name="ck_audio_assets_byte_size"),
        sa.CheckConstraint(
            "duration_ms > 0 AND duration_ms <= 600000", name="ck_audio_assets_duration"
        ),
        sa.CheckConstraint(
            "sample_rate > 0 AND sample_rate <= 192000", name="ck_audio_assets_sample_rate"
        ),
        sa.CheckConstraint("channels > 0 AND channels <= 2", name="ck_audio_assets_channels"),
        sa.CheckConstraint("mime_detected = 'audio/mpeg'", name="ck_audio_assets_mime"),
        sa.CheckConstraint("codec = 'mp3'", name="ck_audio_assets_codec"),
        sa.CheckConstraint(
            "length(checksum_sha256) = 64 AND checksum_sha256 = lower(checksum_sha256)",
            name="ck_audio_assets_checksum",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index("ix_audio_assets_project_id", "audio_assets", ["project_id"])
    op.create_index(
        "ix_audio_assets_project_created_id",
        "audio_assets",
        ["project_id", sa.text("created_at DESC"), sa.text("id DESC")],
    )
    op.create_table(
        "preview_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("edit_spec_revision_id", sa.Uuid(), nullable=False),
        sa.Column("preview_start_ms", sa.BigInteger(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("profile_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("claim_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("artifact_storage_key", sa.String(200), nullable=True),
        sa.Column("artifact_sha256", sa.String(64), nullable=True),
        sa.Column("artifact_byte_size", sa.BigInteger(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.CheckConstraint("preview_start_ms >= 0", name="ck_preview_jobs_start"),
        sa.CheckConstraint(
            "duration_ms >= 1 AND duration_ms <= 8000", name="ck_preview_jobs_duration"
        ),
        sa.CheckConstraint("profile_version >= 1", name="ck_preview_jobs_profile"),
        sa.CheckConstraint("length(idempotency_key) = 64", name="ck_preview_jobs_idempotency"),
        sa.CheckConstraint("attempts >= 0 AND attempts <= 2", name="ck_preview_jobs_attempts"),
        sa.CheckConstraint(
            "(status = 'queued' AND stage = 'queued') OR "
            "(status = 'running' AND stage IN "
            "('preparing', 'encoding', 'validating', 'publishing')) OR "
            "(status = 'completed' AND stage = 'completed') OR "
            "(status = 'failed' AND stage = 'failed')",
            name="ck_preview_jobs_state",
        ),
        sa.CheckConstraint(
            "status != 'running' OR (claim_token IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND heartbeat_at IS NOT NULL AND attempts >= 1)",
            name="ck_preview_jobs_running_lease",
        ),
        sa.CheckConstraint(
            "artifact_byte_size IS NULL OR artifact_byte_size > 0",
            name="ck_preview_jobs_artifact_size",
        ),
        sa.CheckConstraint(
            "artifact_sha256 IS NULL OR (length(artifact_sha256) = 64 "
            "AND artifact_sha256 = lower(artifact_sha256))",
            name="ck_preview_jobs_artifact_checksum",
        ),
        sa.CheckConstraint(
            "status != 'completed' OR (artifact_storage_key IS NOT NULL "
            "AND artifact_sha256 IS NOT NULL AND artifact_byte_size IS NOT NULL "
            "AND finished_at IS NOT NULL)",
            name="ck_preview_jobs_completed_artifact",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["edit_spec_revision_id"], ["edit_spec_revisions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("artifact_storage_key"),
    )
    op.create_index("ix_preview_jobs_project_id", "preview_jobs", ["project_id"])
    op.create_index(
        "ix_preview_jobs_edit_spec_revision_id", "preview_jobs", ["edit_spec_revision_id"]
    )
    op.create_index(
        "ix_preview_jobs_status_created_id", "preview_jobs", ["status", "created_at", "id"]
    )
    op.create_index("ix_preview_jobs_lease", "preview_jobs", ["status", "lease_expires_at"])
    op.create_index(
        "uq_preview_jobs_live_request",
        "preview_jobs",
        ["project_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running', 'completed')"),
        sqlite_where=sa.text("status IN ('queued', 'running', 'completed')"),
    )


def downgrade() -> None:
    # Reverting could delete originals' inventory and jobs or strand v2 snapshots.
    # An explicit, reviewed data migration is needed; never silently discard them.
    raise RuntimeError("R04 downgrade requires a separately reviewed preservation plan.")
