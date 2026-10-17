import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_require_database(monkeypatch):
    monkeypatch.delenv("EDIVO_DATABASE_URL")
    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None)


def test_settings_environment_and_secrets(monkeypatch):
    monkeypatch.setenv("EDIVO_LOG_LEVEL", "WARNING")
    settings = Settings(_env_file=None)
    assert settings.log_level == "WARNING"
    assert "test-only" not in repr(settings)
    assert settings.database_url.get_secret_value().startswith("postgresql+psycopg://")


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///test-only",
        "postgresql://test:test-only@db/edivo",
        "postgresql+psycopg://test@db/edivo",
        "postgresql+psycopg://test:test-only@db/edivo?password=hidden",
        "not-a-url-test-only",
    ],
)
def test_invalid_database_configuration_hides_input(url):
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, database_url=url)
    assert "test-only" not in str(error.value)


@pytest.mark.parametrize("origin", ["*", "https://external.example", "http://localhost/path"])
def test_settings_reject_nonlocal_cors(origin):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, cors_origins=[origin])


def test_timeout_bounds():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ffmpeg_timeout_seconds=0)
