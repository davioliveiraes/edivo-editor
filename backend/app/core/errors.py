"""Public domain errors; internal exception details never become HTTP payloads."""

from pydantic import BaseModel


class AppError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message


class ErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str


def database_unavailable() -> AppError:
    return AppError(503, "database_unavailable", "Banco de dados indisponível. Tente novamente.")
