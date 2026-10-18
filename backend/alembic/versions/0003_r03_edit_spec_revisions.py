"""R03 append-only editing plans; existing projects and media stay unchanged."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0003_r03_edit_spec_revisions"
down_revision = "0002_r02_projects_media"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "edit_spec_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("source_media_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("spec_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision_number >= 1", name="ck_edit_spec_revisions_number"),
        sa.CheckConstraint("schema_version = 1", name="ck_edit_spec_revisions_schema_version"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_media_id"], ["media_assets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "revision_number", name="uq_edit_spec_revisions_project_number"
        ),
    )
    op.create_index("ix_edit_spec_revisions_project_id", "edit_spec_revisions", ["project_id"])
    op.create_index(
        "ix_edit_spec_revisions_source_media_id", "edit_spec_revisions", ["source_media_id"]
    )
    op.execute(
        """
        CREATE FUNCTION edivo_reject_edit_spec_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'edit_spec_revision_immutable' USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER edit_spec_revisions_immutable
        BEFORE UPDATE OR DELETE ON edit_spec_revisions
        FOR EACH ROW EXECUTE FUNCTION edivo_reject_edit_spec_mutation()
        """
    )


def downgrade() -> None:
    # Schema-only reversal. Never execute on persisted user revisions without approval.
    op.execute("DROP TRIGGER edit_spec_revisions_immutable ON edit_spec_revisions")
    op.execute("DROP FUNCTION edivo_reject_edit_spec_mutation()")
    op.drop_index("ix_edit_spec_revisions_source_media_id", table_name="edit_spec_revisions")
    op.drop_index("ix_edit_spec_revisions_project_id", table_name="edit_spec_revisions")
    op.drop_table("edit_spec_revisions")
