"""One bounded multipart source import per request; the storage is never served publicly."""

import tempfile
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from python_multipart.multipart import parse_options_header
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import ClientDisconnect
from starlette.responses import FileResponse

from app.api.dependencies import SessionDependency, get_settings
from app.api.media_stream import PrivateMediaResponse
from app.core.config import Settings
from app.core.errors import AppError, ErrorResponse
from app.core.upload_limits import UploadBodyExceeded
from app.db.session import safe_rollback
from app.schemas.media import MediaPage, MediaResponse
from app.services.media import get_media, import_media, list_media, require_active_project
from app.storage.local import LocalStorage

router = APIRouter(
    prefix="/api/v1/projects",
    tags=["media"],
    responses={status: {"model": ErrorResponse} for status in (400, 404, 413, 415, 422, 503)},
)
SettingsDependency = Annotated[Settings, Depends(get_settings)]


class SingleFileParser(MultiPartParser):
    """Starlette parser constrained before untrusted headers or file data accumulate."""

    def __init__(self, request: Request, settings: Settings) -> None:
        super().__init__(request.headers, request.stream(), max_files=1, max_fields=0)
        self.settings = settings
        self.storage = LocalStorage(settings)
        self.temp_directory = self.storage.prepare_temp()
        self.header_bytes = 0
        self.file_bytes = 0
        self.finished = False

    def on_part_begin(self) -> None:
        self.header_bytes = 0
        super().on_part_begin()

    def _header_limit(self, start: int, end: int) -> None:
        self.header_bytes += end - start
        if self.header_bytes > 8192:
            raise MultiPartException("multipart_headers_exceeded")

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._header_limit(start, end)
        super().on_header_field(data, start, end)

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._header_limit(start, end)
        super().on_header_value(data, start, end)

    def on_headers_finished(self) -> None:
        super().on_headers_finished()
        upload = self._current_part.file
        if upload is None or self._current_part.field_name != "file" or not upload.filename:
            raise MultiPartException("expected_single_file_field")
        # Explicit directory avoids the container's deliberately small /tmp tmpfs.
        upload.file.close()
        # Ownership transfers to UploadFile and parse/finally always closes it.
        spool = tempfile.SpooledTemporaryFile(  # noqa: SIM115
            max_size=self.spool_max_size, dir=self.temp_directory
        )
        upload.file = spool
        self._files_to_close_on_error[-1] = spool

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        self.file_bytes += end - start
        if self.file_bytes > self.settings.upload_max_bytes:
            raise UploadBodyExceeded
        self.storage.ensure_space(end - start)
        super().on_part_data(data, start, end)

    def on_end(self) -> None:
        self.finished = True


@router.post(
    "/{project_id}/media",
    response_model=MediaResponse,
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
async def upload_media(
    project_id: UUID, request: Request, session: SessionDependency, settings: SettingsDependency
) -> MediaResponse:
    await run_in_threadpool(require_active_project, session, project_id)
    safe_rollback(session)
    content_type = request.headers.get("content-type", "")
    if len(content_type) > 1024:
        raise AppError(400, "invalid_multipart", "Requisição multipart inválida.")
    media_type, params = parse_options_header(content_type)
    boundary = params.get(b"boundary", b"")
    if media_type != b"multipart/form-data" or not 1 <= len(boundary) <= 200:
        raise AppError(400, "invalid_multipart", "Envie um único arquivo no campo file.")
    parser = SingleFileParser(request, settings)
    form = None
    try:
        form = await parser.parse()
        if not parser.finished or len(form.multi_items()) != 1:
            raise MultiPartException("incomplete_multipart")
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise MultiPartException("expected_single_file")
        asset = await run_in_threadpool(
            import_media, session, settings, project_id, upload.filename, upload.file
        )
        return MediaResponse.model_validate(asset)
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


@router.get("/{project_id}/media", response_model=MediaPage)
def project_media(
    project_id: UUID,
    session: SessionDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MediaPage:
    items, total = list_media(session, project_id, limit, offset)
    return MediaPage(
        items=[MediaResponse.model_validate(asset) for asset in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{project_id}/media/{media_id}", response_model=MediaResponse)
def project_media_detail(
    project_id: UUID, media_id: UUID, session: SessionDependency
) -> MediaResponse:
    return MediaResponse.model_validate(get_media(session, project_id, media_id))


STREAM_RESPONSES = {
    200: {"content": {"video/mp4": {"schema": {"type": "string", "format": "binary"}}}},
    206: {
        "description": "Um intervalo de bytes do MP4 original.",
        "content": {"video/mp4": {"schema": {"type": "string", "format": "binary"}}},
    },
    416: {"model": ErrorResponse, "description": "Range inválido, múltiplo ou insatisfatível."},
}


@router.get(
    "/{project_id}/media/{media_id}/stream",
    response_class=FileResponse,
    responses=STREAM_RESPONSES,
)
@router.head(
    "/{project_id}/media/{media_id}/stream",
    response_class=FileResponse,
    responses=STREAM_RESPONSES,
)
def stream_media(
    project_id: UUID,
    media_id: UUID,
    session: SessionDependency,
    settings: SettingsDependency,
) -> FileResponse:
    asset = get_media(session, project_id, media_id)
    if asset.kind != "source_video" or asset.mime_detected != "video/mp4":
        raise AppError(503, "media_unavailable", "Vídeo temporariamente indisponível.")
    original = LocalStorage(settings).open_original(
        asset.storage_key, project_id, media_id, asset.byte_size
    )
    try:
        response = PrivateMediaResponse(original, asset.checksum_sha256)
        # A long transfer must not retain a PostgreSQL connection/transaction.
        safe_rollback(session)
        return response
    except BaseException:
        original.file.close()
        raise
