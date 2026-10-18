from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.edit_spec_revision import EditSpecRevision


class EditSpecRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def current(self, project_id: UUID) -> EditSpecRevision | None:
        return self.session.scalar(
            select(EditSpecRevision)
            .where(EditSpecRevision.project_id == project_id)
            .order_by(EditSpecRevision.revision_number.desc())
            .limit(1)
        )

    def add(self, revision: EditSpecRevision) -> None:
        self.session.add(revision)
