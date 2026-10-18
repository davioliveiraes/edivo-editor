from uuid import UUID

from fastapi import APIRouter, Response

from app.api.dependencies import SessionDependency
from app.core.errors import ErrorResponse
from app.schemas.edit_spec import EditSpecPut, EditSpecResponse
from app.services.edit_specs import EditSpecService

router = APIRouter(
    prefix="/api/v1/projects",
    tags=["edit-spec"],
    responses={status: {"model": ErrorResponse} for status in (404, 409, 422, 503)},
)


@router.get("/{project_id}/edit-spec", response_model=EditSpecResponse)
def current_edit_spec(project_id: UUID, session: SessionDependency) -> EditSpecResponse:
    return EditSpecService(session).get(project_id)


@router.put(
    "/{project_id}/edit-spec",
    response_model=EditSpecResponse,
    responses={201: {"model": EditSpecResponse, "description": "Primeira revisão criada."}},
)
def save_edit_spec(
    project_id: UUID, data: EditSpecPut, response: Response, session: SessionDependency
) -> EditSpecResponse:
    revision, created = EditSpecService(session).put(project_id, data)
    response.status_code = 201 if created else 200
    return revision
