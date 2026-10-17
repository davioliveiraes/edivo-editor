from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.media_asset import MediaAsset


class MediaRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, asset: MediaAsset) -> None:
        self.session.add(asset)

    def get_for_project(self, project_id: UUID, media_id: UUID) -> MediaAsset | None:
        return self.session.scalar(
            select(MediaAsset).where(MediaAsset.id == media_id, MediaAsset.project_id == project_id)
        )

    def list_for_project(
        self, project_id: UUID, limit: int, offset: int
    ) -> tuple[list[MediaAsset], int]:
        total = self.session.scalar(
            select(func.count()).select_from(MediaAsset).where(MediaAsset.project_id == project_id)
        )
        query = (
            select(MediaAsset)
            .where(MediaAsset.project_id == project_id)
            .order_by(MediaAsset.created_at.desc(), MediaAsset.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(query)), int(total or 0)
