"""Native Starlette Range streaming over an already authorized, pinned descriptor.

FileResponse 1.8 provides bounded reads, HEAD and disconnect handling. Its default
pathname reopen, multipart ranges and malformed-range 400 differ from our private
media contract, so this small adapter changes only those boundaries.
"""

import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

import anyio
from starlette.datastructures import Headers
from starlette.responses import (
    FileResponse,
    JSONResponse,
    MalformedRangeHeader,
    RangeNotSatisfiable,
)
from starlette.types import Receive, Scope, Send

from app.storage.local import OpenedOriginal

logger = logging.getLogger("edivo.media")
PRIVATE_HEADERS = {
    "Accept-Ranges": "bytes",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private, no-store",
    "Content-Disposition": "inline",
}
SINGLE_RANGE = re.compile(r"bytes=[0-9]*-[0-9]*", re.IGNORECASE | re.ASCII)


class PrivateMediaResponse(FileResponse):
    def __init__(
        self,
        original: OpenedOriginal,
        checksum_sha256: str,
        *,
        filename: str | None = None,
        disposition: Literal["inline", "attachment"] = "inline",
    ) -> None:
        self.original = original
        self.private_headers = {**PRIVATE_HEADERS}
        if disposition == "attachment":
            if (
                filename is None
                or re.fullmatch(
                    r"edivo-r[1-9][0-9]*-(9x16|1x1|4x5|16x9)-(standard|high)\.mp4", filename
                )
                is None
            ):
                raise ValueError("invalid_render_download_filename")
            self.private_headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        elif disposition != "inline" or filename is not None:
            raise ValueError("invalid_media_disposition")
        super().__init__(
            # The path is never opened or sent to the server: all reads use the FD.
            path="source.mp4",
            media_type="video/mp4",
            stat_result=original.stat_result,
            headers={**self.private_headers, "ETag": f'"sha256:{checksum_sha256}"'},
        )

    def _valid_range(self, headers: Headers) -> bool:
        values = headers.getlist("range")
        if not values:
            return True
        if len(values) != 1:
            return False
        value = values[0].strip()
        if len(values[0]) > 256 or SINGLE_RANGE.fullmatch(value) is None:
            return False
        try:
            return len(self._parse_range_header(value, self.original.stat_result.st_size)) == 1
        except (MalformedRangeHeader, RangeNotSatisfiable, ValueError):
            return False

    @asynccontextmanager
    async def _open_file(self) -> AsyncIterator[anyio.AsyncFile[bytes]]:
        # __call__ closes even HEAD, rejection or cancellation before body iteration.
        yield anyio.wrap_file(self.original.file)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            if not self._valid_range(Headers(scope=scope)):
                response = JSONResponse(
                    status_code=416,
                    content={
                        "code": "range_not_satisfiable",
                        "message": "Intervalo de bytes inválido.",
                        "request_id": scope.get("state", {}).get("request_id", ""),
                    },
                    headers={
                        **self.private_headers,
                        "Content-Range": f"bytes */{self.original.stat_result.st_size}",
                    },
                )
                if scope["method"] == "HEAD":
                    # Keep the corresponding GET Content-Length, but emit no body.
                    response.body = b""
                await response(scope, receive, send)
                return
            # A server-side pathsend would reopen a pathname after validation.
            safe_scope = {
                **scope,
                "extensions": {
                    name: value
                    for name, value in scope.get("extensions", {}).items()
                    if name != "http.response.pathsend"
                },
            }
            await super().__call__(safe_scope, receive, send)
        finally:
            # Shield closure from client disconnect or cancellation of the request.
            with anyio.CancelScope(shield=True):
                try:
                    await anyio.to_thread.run_sync(self.original.file.close)
                except OSError:
                    logger.warning("original_descriptor_close_failed")
