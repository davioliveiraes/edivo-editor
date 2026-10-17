from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from app import __version__
from app.integrations.ffmpeg.capabilities import CapabilitiesReport
from app.services.readiness import ReadinessReport, check_dependencies

router = APIRouter(prefix="/api/v1/system", tags=["system"])


class HealthReport(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["edivo-api"] = "edivo-api"
    version: str = __version__


@router.get("/health", response_model=HealthReport)
def health() -> HealthReport:
    return HealthReport()


@router.get("/ready", response_model=ReadinessReport, responses={503: {"model": ReadinessReport}})
def ready(request: Request, response: Response) -> ReadinessReport:
    result = check_dependencies(
        request.app.state.database.check, request.app.state.settings.storage_root
    )
    if result.status == "not_ready":
        response.status_code = 503
    return result


@router.get("/capabilities", response_model=CapabilitiesReport)
def capabilities(request: Request) -> CapabilitiesReport:
    return request.app.state.capabilities.get()
