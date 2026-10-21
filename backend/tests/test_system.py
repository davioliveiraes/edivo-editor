from pathlib import Path

import pytest
from fastapi import HTTPException


def test_health_does_not_check_database(client, application, monkeypatch):
    def forbidden():
        pytest.fail("Liveness must not connect to PostgreSQL")

    monkeypatch.setattr(application.state.database, "check", forbidden)
    response = client.get("/api/v1/system/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "edivo-api", "version": "0.1.0"}
    assert response.headers["x-request-id"]


@pytest.mark.parametrize("database_ok,status", [(True, 200), (False, 503)])
def test_readiness_database_states(client, application, monkeypatch, database_ok, status):
    monkeypatch.setattr(application.state.database, "check", lambda: database_ok)
    response = client.get("/api/v1/system/ready")
    assert response.status_code == status
    assert response.json() == {
        "status": "ready" if database_ok else "not_ready",
        "checks": {"database": "ok" if database_ok else "unavailable", "storage": "ok"},
    }


def test_readiness_storage_failure(client, application, monkeypatch, tmp_path: Path):
    application.state.settings.storage_root = tmp_path / "not-present"
    monkeypatch.setattr(application.state.database, "check", lambda: True)
    response = client.get("/api/v1/system/ready")
    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "ok", "storage": "unavailable"}
    assert str(tmp_path) not in response.text


def test_capabilities_missing_binary_is_explicit(client, application, settings):
    from app.integrations.ffmpeg.capabilities import CapabilityDetector

    settings.ffmpeg_bin = "/not-installed/ffmpeg"
    settings.ffprobe_bin = "/not-installed/ffprobe"
    application.state.capabilities = CapabilityDetector(settings)
    response = client.get("/api/v1/system/capabilities")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "unavailable"
    assert data["runtime"] == "api"
    assert data["ffmpeg"] == {"available": False, "reason": "not_found", "version": None}
    assert not data["encoders"]["h264"]["available"]
    assert "/not-installed" not in response.text
    assert "worker" not in data


def test_unexpected_error_is_sanitized(client, application):
    @application.get("/test-internal-error", include_in_schema=False)
    def boom():
        raise RuntimeError("postgresql+psycopg://private:password@db/private /home/private/file")

    response = client.get("/test-internal-error")
    assert response.status_code == 500
    assert response.json() == {
        "code": "internal_error",
        "message": "Não foi possível concluir a solicitação.",
        "request_id": response.headers["x-request-id"],
    }
    assert "password" not in response.text
    assert "private" not in response.text
    assert "Traceback" not in response.text


def test_http_errors_do_not_echo_details(client, application):
    @application.get("/test-http-error", include_in_schema=False)
    def denied():
        raise HTTPException(status_code=403, detail="secret /home/private")

    response = client.get("/test-http-error")
    assert response.status_code == 403
    assert response.json()["code"] == "http_error"
    assert "private" not in response.text


def test_validation_errors_do_not_echo_input(client, application):
    @application.get("/test-validation", include_in_schema=False)
    def validated(value: int):
        return value

    response = client.get("/test-validation?value=secret-password")
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert "secret-password" not in response.text


def test_cors_allowlist(client):
    allowed = client.get("/api/v1/system/health", headers={"Origin": "http://127.0.0.1:5173"})
    denied = client.get("/api/v1/system/health", headers={"Origin": "https://external.example"})
    assert allowed.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
    assert "access-control-allow-origin" not in denied.headers


def test_no_future_api_routes(application):
    paths = set(application.openapi()["paths"])
    assert {path for path in paths if path.startswith("/api/")} == {
        "/api/v1/system/health",
        "/api/v1/system/ready",
        "/api/v1/system/capabilities",
        "/api/v1/projects",
        "/api/v1/projects/{project_id}",
        "/api/v1/projects/{project_id}/media",
        "/api/v1/projects/{project_id}/media/{media_id}",
        "/api/v1/projects/{project_id}/media/{media_id}/stream",
        "/api/v1/projects/{project_id}/edit-spec",
        "/api/v1/projects/{project_id}/audio",
        "/api/v1/projects/{project_id}/audio/{audio_id}",
        "/api/v1/projects/{project_id}/previews",
        "/api/v1/projects/{project_id}/previews/{preview_id}",
        "/api/v1/projects/{project_id}/previews/{preview_id}/file",
    }
