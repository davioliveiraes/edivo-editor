"""One bounded multipart MP3 import per request; the storage is never served publicly."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from python_multipart.multipart import parse_options_header
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException
from starlette.requests import ClientDisconnect

from app.api.dependencies import SessionDependency, get_settings
from app.api.v1.media import SingleFileParser
from app.core.config import Settings
from app.core.errors import AppError, ErrorResponse
from app.core.upload_limits import UploadBodyExceeded
from app.db.session import safe_rollback
from app.schemas.audio import AudioPage, AudioResponse
from app.services.audio import get_audio, import_audio, list_audio, require_active_project

router = APIRouter(
    prefix="/api/v1/projects",
    tags=["audio"],
    responses={status: {"model": ErrorResponse} for status in (400, 404, 413, 415, 422, 503)},
)
SettingsDependency = Annotated[Settings, Depends(get_settings)]


@router.post(
    "/{project_id}/audio",
    response_model=AudioResponse,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "additionalProperties": False,
                        "properties": {"file": {"type": "string", "format": "binary"}},
                    }
                }
            },
        }
    },
)
async def upload_audio(
    project_id: UUID, request: Request, session: SessionDependency, settings: SettingsDependency
) -> AudioResponse:
    await run_in_threadpool(require_active_project, session, project_id)
    safe_rollback(session)
    content_type = request.headers.get("content-type", "")
    if len(content_type) > 1024:
        raise AppError(400, "invalid_multipart", "Requisição multipart inválida.")
    media_type, params = parse_options_header(content_type)
    boundary = params.get(b"boundary", b"")
    if media_type != b"multipart/form-data" or not 1 <= len(boundary) <= 200:
        raise AppError(400, "invalid_multipart", "Envie um único arquivo no campo file.")
    parser = SingleFileParser(request, settings, max_file_bytes=settings.audio_upload_max_bytes)
    form = None
    try:
        form = await parser.parse()
        if not parser.finished or len(form.multi_items()) != 1:
            raise MultiPartException("incomplete_multipart")
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise MultiPartException("expected_single_file")
        asset = await run_in_threadpool(
            import_audio, session, settings, project_id, upload.filename, upload.file
        )
        return AudioResponse.model_validate(asset)
    except UploadBodyExceeded:
        raise AppError(413, "upload_too_large", "O arquivo excede o limite permitido.") from None
    except (MultiPartException, ClientDisconnect):
        raise AppError(
            400, "invalid_multipart", "Upload multipart incompleto ou inválido."
        ) from None
    except OSError:
        raise AppError(
            503, "storage_unavailable", "Armazenamento temporariamente indisponível."
        ) from None
    finally:
        if form is not None:
            await form.close()
        # Also close spools if post-parse validation failed or the client disconnected.
        for spool in parser._files_to_close_on_error:
            spool.close()


@router.get("/{project_id}/audio", response_model=AudioPage)
def project_audio(
    project_id: UUID,
    session: SessionDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AudioPage:
    items, total = list_audio(session, project_id, limit, offset)
    return AudioPage(
        items=[AudioResponse.model_validate(asset) for asset in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{project_id}/audio/{audio_id}", response_model=AudioResponse)
def project_audio_detail(
    project_id: UUID, audio_id: UUID, session: SessionDependency
) -> AudioResponse:
    return AudioResponse.model_validate(get_audio(session, project_id, audio_id))
