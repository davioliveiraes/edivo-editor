from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.audio_asset import AudioAsset


class AudioRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, asset: AudioAsset) -> None:
        self.session.add(asset)

    def get_for_project(self, project_id: UUID, audio_id: UUID) -> AudioAsset | None:
        return self.session.scalar(
            select(AudioAsset).where(AudioAsset.id == audio_id, AudioAsset.project_id == project_id)
        )

    def list_for_project(
        self, project_id: UUID, limit: int, offset: int
    ) -> tuple[list[AudioAsset], int]:
        total = self.session.scalar(
            select(func.count()).select_from(AudioAsset).where(AudioAsset.project_id == project_id)
        )
        query = (
            select(AudioAsset)
            .where(AudioAsset.project_id == project_id)
            .order_by(AudioAsset.created_at.desc(), AudioAsset.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(query)), int(total or 0)
