"""Bound incoming multipart bodies before the parser can spool an unlimited request."""

import re
from uuid import uuid4

from starlette.formparsers import MultiPartException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class UploadBodyExceeded(MultiPartException):
    def __init__(self) -> None:
        super().__init__("upload_body_exceeded")


class UploadBodyLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        max_body_bytes: int,
        audio_max_body_bytes: int = 30 * 1024 * 1024 + 65536,
    ) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.audio_max_body_bytes = audio_max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        is_upload = (
            scope["type"] == "http"
            and scope["method"] == "POST"
            and re.fullmatch(r"/api/v1/projects/[^/]+/(?:media|audio)/?", scope["path"]) is not None
        )
        if not is_upload:
            await self.app(scope, receive, send)
            return

        max_body_bytes = (
            self.audio_max_body_bytes
            if scope["path"].rstrip("/").endswith("/audio")
            else self.max_body_bytes
        )
        lengths = [value for name, value in scope["headers"] if name.lower() == b"content-length"]
        if lengths:
            try:
                # Ambiguous Content-Length values are never accepted.
                if len(lengths) != 1 or not lengths[0].isdigit():
                    raise ValueError
                declared_length = int(lengths[0])
            except (ValueError, OverflowError):
                await self._reject(scope, receive, send, 400)
                return
            if declared_length > max_body_bytes:
                await self._reject(scope, receive, send, 413)
                return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > max_body_bytes:
                    # The parser closes every spool for any exception in parse().
                    raise UploadBodyExceeded
            return message

        await self.app(scope, limited_receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send, status: int) -> None:
        request_id = scope.get("state", {}).get("request_id", str(uuid4()))
        response = JSONResponse(
            status_code=status,
            content={
                "code": "upload_too_large" if status == 413 else "invalid_multipart",
                "message": "O arquivo excede o limite permitido."
                if status == 413
                else "Requisição multipart inválida.",
                "request_id": request_id,
            },
        )
        await response(scope, receive, send)
