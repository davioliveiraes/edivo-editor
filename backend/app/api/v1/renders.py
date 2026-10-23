"""Final export state and private downloads; HTTP never starts or signals FFmpeg."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from starlette.responses import FileResponse

from app.api.dependencies import SessionDependency, get_settings
from app.api.media_stream import PrivateMediaResponse
from app.core.config import Settings
from app.core.errors import ErrorResponse
from app.db.session import safe_rollback
from app.schemas.renders import RenderCreate, RenderPage, RenderResponse, RenderStatus
from app.services.renders import RenderService

router = APIRouter(
    prefix="/api/v1/projects",
    tags=["renders"],
    responses={status: {"model": ErrorResponse} for status in (404, 409, 422, 503)},
)
SettingsDependency = Annotated[Settings, Depends(get_settings)]


@router.post(
    "/{project_id}/renders",
    response_model=RenderResponse,
    status_code=202,
    responses={200: {"model": RenderResponse}},
)
def create_render(
    project_id: UUID,
    data: RenderCreate,
    response: Response,
    session: SessionDependency,
    settings: SettingsDependency,
) -> RenderResponse:
    result, available = RenderService(session, settings).create(project_id, data)
    response.status_code = 200 if available else 202
    return result


@router.get("/{project_id}/renders", response_model=RenderPage)
def render_history(
    project_id: UUID,
    session: SessionDependency,
    settings: SettingsDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: RenderStatus | None = None,
) -> RenderPage:
    return RenderService(session, settings).list(project_id, limit, offset, status)


@router.get("/{project_id}/renders/{render_id}", response_model=RenderResponse)
def render_status(
    project_id: UUID, render_id: UUID, session: SessionDependency, settings: SettingsDependency
) -> RenderResponse:
    return RenderService(session, settings).get(project_id, render_id)


@router.post(
    "/{project_id}/renders/{render_id}/cancel",
    response_model=RenderResponse,
    responses={202: {"model": RenderResponse}},
)
def cancel_render(
    project_id: UUID,
    render_id: UUID,
    response: Response,
    session: SessionDependency,
    settings: SettingsDependency,
) -> RenderResponse:
    result, pending = RenderService(session, settings).cancel(project_id, render_id)
    response.status_code = 202 if pending else 200
    return result


@router.head("/{project_id}/renders/{render_id}/file", response_class=FileResponse)
@router.get(
    "/{project_id}/renders/{render_id}/file",
    response_class=FileResponse,
    responses={
        200: {"content": {"video/mp4": {}}},
        206: {"content": {"video/mp4": {}}},
        416: {"model": ErrorResponse},
    },
)
def render_file(
    project_id: UUID, render_id: UUID, session: SessionDependency, settings: SettingsDependency
) -> PrivateMediaResponse:
    opened, checksum, filename = RenderService(session, settings).open_file(project_id, render_id)
    try:
        safe_rollback(session)
        return PrivateMediaResponse(opened, checksum, filename=filename, disposition="attachment")
    except BaseException:
        opened.file.close()
        raise
