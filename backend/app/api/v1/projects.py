from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.api.dependencies import SessionDependency
from app.core.errors import ErrorResponse
from app.schemas.projects import ProjectCreate, ProjectPage, ProjectResponse, ProjectUpdate
from app.services.projects import ProjectService

router = APIRouter(
    prefix="/api/v1/projects",
    tags=["projects"],
    responses={
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)


@router.post("", response_model=ProjectResponse, status_code=201)
def create_project(data: ProjectCreate, session: SessionDependency) -> ProjectResponse:
    return ProjectResponse.model_validate(ProjectService(session).create(data))


@router.get("", response_model=ProjectPage)
def list_projects(
    session: SessionDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProjectPage:
    projects, total = ProjectService(session).list(limit, offset)
    return ProjectPage(
        items=[ProjectResponse.model_validate(project) for project in projects],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(project_id: UUID, session: SessionDependency) -> ProjectResponse:
    return ProjectResponse.model_validate(ProjectService(session).get(project_id))


@router.patch("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: UUID, data: ProjectUpdate, session: SessionDependency
) -> ProjectResponse:
    return ProjectResponse.model_validate(ProjectService(session).update(project_id, data))


@router.delete("/{project_id}", status_code=204, response_class=Response)
def delete_project(project_id: UUID, session: SessionDependency) -> Response:
    ProjectService(session).delete(project_id)
    return Response(status_code=204)
