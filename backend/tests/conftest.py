import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Importing the ASGI entrypoint must never require local secrets in unit tests.
os.environ["EDIVO_DATABASE_URL"] = "postgresql+psycopg://test:test-only@127.0.0.1/edivo_test"

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        env="test",
        database_url="postgresql+psycopg://test:test-only@127.0.0.1/edivo_test",
        storage_root=tmp_path,
    )


@pytest.fixture
def application(settings):
    return create_app(settings)


@pytest.fixture
def client(application):
    with TestClient(application, raise_server_exceptions=False) as test_client:
        yield test_client
