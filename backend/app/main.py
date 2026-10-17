import logging
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
from pydantic_settings import SettingsError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from app import __version__
from app.api.v1.system import router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import Database
from app.integrations.ffmpeg.capabilities import CapabilityDetector

logger = logging.getLogger("edivo.api")


def create_app(settings: Settings | None = None) -> FastAPI:
    try:
        settings = settings or get_settings()
    except (ValidationError, SettingsError):
        raise RuntimeError("Configuração Edivo inválida. Verifique as variáveis EDIVO_.") from None
    configure_logging(settings.log_level)
    database = Database(settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        logger.info("api_started")
        try:
            yield
        finally:
            database.close()
            logger.info("api_stopped")

    application = FastAPI(title="Edivo API", version=__version__, lifespan=lifespan)
    application.state.settings = settings
    application.state.database = database
    application.state.capabilities = CapabilityDetector(settings)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET"],
        allow_headers=["Accept", "Content-Type"],
    )

    @application.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = str(uuid4())
        request.state.request_id = request_id
        started = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.error("request_failed", extra={"request_id": request_id})
            response = JSONResponse(
                status_code=500,
                content={
                    "code": "internal_error",
                    "message": "Não foi possível concluir a solicitação.",
                    "request_id": request_id,
                },
            )
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request_completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "status_code": response.status_code,
                "duration_ms": round((perf_counter() - started) * 1000, 2),
            },
        )
        return response

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        return JSONResponse(
            status_code=error.status_code,
            content={
                "code": "http_error",
                "message": "Recurso não encontrado."
                if error.status_code == 404
                else "Solicitação recusada.",
                "request_id": request.state.request_id,
            },
            headers=error.headers,
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "code": "validation_error",
                "message": "Solicitação inválida.",
                "request_id": request.state.request_id,
            },
        )

    application.include_router(router)
    return application


app = create_app()
