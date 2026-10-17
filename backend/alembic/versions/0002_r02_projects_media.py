"""R02 projects and private original-media inventory, following the R01 baseline."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002_r02_projects_media"
down_revision = "0001_r01_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("purpose", sa.String(500), nullable=True),
        sa.Column("target_preset", sa.String(4), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("length(trim(name)) BETWEEN 1 AND 120", name="ck_projects_name"),
        sa.CheckConstraint("purpose IS NULL OR length(purpose) <= 500", name="ck_projects_purpose"),
        sa.CheckConstraint(
            "target_preset IS NULL OR target_preset IN ('9:16', '1:1', '4:5', '16:9')",
            name="ck_projects_target_preset",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_projects_active_created_id",
        "projects",
        [sa.text("created_at DESC"), sa.text("id DESC")],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "media_assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("original_filename", sa.String(160), nullable=False),
        sa.Column("storage_key", sa.String(200), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("mime_detected", sa.String(40), nullable=False),
        sa.Column("duration_ms", sa.BigInteger(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("fps_num", sa.Integer(), nullable=False),
        sa.Column("fps_den", sa.Integer(), nullable=False),
        sa.Column("video_codec", sa.String(64), nullable=True),
        sa.Column("has_audio", sa.Boolean(), nullable=False),
        sa.Column("audio_codec", sa.String(64), nullable=True),
        sa.Column("probe_metadata", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind = 'source_video'", name="ck_media_assets_kind"),
        sa.CheckConstraint("byte_size > 0", name="ck_media_assets_byte_size"),
        sa.CheckConstraint("duration_ms > 0", name="ck_media_assets_duration_ms"),
        sa.CheckConstraint("width > 0 AND height > 0", name="ck_media_assets_dimensions"),
        sa.CheckConstraint("fps_num > 0 AND fps_den > 0", name="ck_media_assets_fps"),
        sa.CheckConstraint(
            "length(checksum_sha256) = 64 AND checksum_sha256 = lower(checksum_sha256)",
            name="ck_media_assets_checksum",
        ),
        sa.CheckConstraint("mime_detected = 'video/mp4'", name="ck_media_assets_mime"),
        sa.CheckConstraint("has_audio OR audio_codec IS NULL", name="ck_media_assets_audio"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index("ix_media_assets_project_id", "media_assets", ["project_id"])
    op.create_index(
        "ix_media_assets_project_created_id",
        "media_assets",
        ["project_id", sa.text("created_at DESC"), sa.text("id DESC")],
    )


def downgrade() -> None:
    # Deliberately schema-only: never run against user data without explicit approval.
    op.drop_index("ix_media_assets_project_created_id", table_name="media_assets")
    op.drop_index("ix_media_assets_project_id", table_name="media_assets")
    op.drop_table("media_assets")
    op.drop_index("ix_projects_active_created_id", table_name="projects")
    op.drop_table("projects")
