"""Projects remain in the database after an explicit logical deletion."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Index, String, Uuid, desc, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.media_asset import MediaAsset


def utc_now() -> datetime:
    return datetime.now(UTC)


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("length(trim(name)) BETWEEN 1 AND 120", name="ck_projects_name"),
        CheckConstraint("purpose IS NULL OR length(purpose) <= 500", name="ck_projects_purpose"),
        CheckConstraint(
            "target_preset IS NULL OR target_preset IN ('9:16', '1:1', '4:5', '16:9')",
            name="ck_projects_target_preset",
        ),
        Index(
            "ix_projects_active_created_id",
            desc("created_at"),
            desc("id"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    purpose: Mapped[str | None] = mapped_column(String(500))
    target_preset: Mapped[str | None] = mapped_column(String(4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    media_assets: Mapped[list["MediaAsset"]] = relationship(
        back_populates="project", passive_deletes=True
    )
