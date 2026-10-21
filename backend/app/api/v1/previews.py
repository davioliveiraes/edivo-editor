from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from starlette.responses import FileResponse

from app.api.dependencies import SessionDependency, get_settings
from app.api.media_stream import PrivateMediaResponse
from app.core.config import Settings
from app.core.errors import ErrorResponse
from app.db.session import safe_rollback
from app.schemas.previews import PreviewCreate, PreviewResponse
from app.services.previews import PreviewService

router = APIRouter(
    prefix="/api/v1/projects",
    tags=["previews"],
    responses={status: {"model": ErrorResponse} for status in (404, 409, 422, 503)},
)
SettingsDependency = Annotated[Settings, Depends(get_settings)]


@router.post(
    "/{project_id}/previews",
    response_model=PreviewResponse,
    status_code=202,
    responses={200: {"model": PreviewResponse}},
)
def create_preview(
    project_id: UUID,
    data: PreviewCreate,
    response: Response,
    session: SessionDependency,
    settings: SettingsDependency,
) -> PreviewResponse:
    result, available = PreviewService(session, settings).create(project_id, data)
    response.status_code = 200 if available else 202
    return result


@router.get("/{project_id}/previews/{preview_id}", response_model=PreviewResponse)
def preview_status(
    project_id: UUID, preview_id: UUID, session: SessionDependency, settings: SettingsDependency
) -> PreviewResponse:
    return PreviewService(session, settings).get(project_id, preview_id)


@router.head(
    "/{project_id}/previews/{preview_id}/file",
    response_class=FileResponse,
)
@router.get(
    "/{project_id}/previews/{preview_id}/file",
    response_class=FileResponse,
    responses={
        200: {"content": {"video/mp4": {}}},
        206: {"content": {"video/mp4": {}}},
        416: {"model": ErrorResponse},
    },
)
def preview_file(
    project_id: UUID, preview_id: UUID, session: SessionDependency, settings: SettingsDependency
) -> PrivateMediaResponse:
    opened, checksum = PreviewService(session, settings).open_file(project_id, preview_id)
    try:
        safe_rollback(session)
        return PrivateMediaResponse(opened, checksum)
    except BaseException:
        opened.file.close()
        raise
