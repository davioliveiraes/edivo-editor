"""Append-only snapshots of the current editing plan; originals remain untouched."""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    UniqueConstraint,
    Uuid,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.project import utc_now

if TYPE_CHECKING:
    from app.models.media_asset import MediaAsset
    from app.models.project import Project


class EditSpecRevision(Base):
    __tablename__ = "edit_spec_revisions"
    __table_args__ = (
        CheckConstraint("revision_number >= 1", name="ck_edit_spec_revisions_number"),
        CheckConstraint("schema_version IN (1, 2)", name="ck_edit_spec_revisions_schema_version"),
        UniqueConstraint(
            "project_id", "revision_number", name="uq_edit_spec_revisions_project_number"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_media_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("media_assets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    spec_json: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    project: Mapped["Project"] = relationship()
    source_media: Mapped["MediaAsset"] = relationship()


@event.listens_for(EditSpecRevision, "before_update")
@event.listens_for(EditSpecRevision, "before_delete")
def reject_revision_mutation(_mapper, _connection, _target) -> None:
    # The PostgreSQL migration also protects direct SQL writes. This guard gives
    # ORM callers the same append-only behavior, including isolated SQLite tests.
    raise ValueError("edit_spec_revision_immutable")
