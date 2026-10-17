from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.project import Project


class ProjectRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_active(self, project_id: UUID, *, for_update: bool = False) -> Project | None:
        query = select(Project).where(Project.id == project_id, Project.deleted_at.is_(None))
        if for_update:
            query = query.with_for_update()
        return self.session.scalar(query)

    def list_active(self, limit: int, offset: int) -> tuple[list[Project], int]:
        total = self.session.scalar(
            select(func.count()).select_from(Project).where(Project.deleted_at.is_(None))
        )
        query = (
            select(Project)
            .where(Project.deleted_at.is_(None))
            .order_by(Project.created_at.desc(), Project.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(query)), int(total or 0)

    def add(self, project: Project) -> None:
        self.session.add(project)
