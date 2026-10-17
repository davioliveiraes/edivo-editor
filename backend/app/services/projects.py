from uuid import UUID

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.project import Project, utc_now
from app.repositories.projects import ProjectRepository
from app.schemas.projects import ProjectCreate, ProjectUpdate


class ProjectService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = ProjectRepository(session)

    def create(self, data: ProjectCreate) -> Project:
        project = Project(**data.model_dump())
        self.repository.add(project)
        self.session.commit()
        return project

    def list(self, limit: int, offset: int) -> tuple[list[Project], int]:
        return self.repository.list_active(limit, offset)

    def get(self, project_id: UUID, *, for_update: bool = False) -> Project:
        project = self.repository.get_active(project_id, for_update=for_update)
        if project is None:
            raise AppError(404, "project_not_found", "Projeto não encontrado.")
        return project

    def update(self, project_id: UUID, data: ProjectUpdate) -> Project:
        project = self.get(project_id, for_update=True)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(project, field, value)
        project.updated_at = utc_now()
        self.session.commit()
        return project

    def delete(self, project_id: UUID) -> None:
        project = self.get(project_id, for_update=True)
        project.deleted_at = utc_now()
        project.updated_at = project.deleted_at
        self.session.commit()
