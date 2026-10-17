import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Importing the ASGI entrypoint must never require local secrets in unit tests.
os.environ["EDIVO_DATABASE_URL"] = "postgresql+psycopg://test:test-only@127.0.0.1/edivo_test"

from app.core.config import Settings  # noqa: E402
from app.db.base import Base  # noqa: E402
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


@pytest.fixture
def domain_sessions():
    """Isolated relational tests; real PostgreSQL is validated separately by Compose QA."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def enforce_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def domain_application(application, domain_sessions, monkeypatch):
    monkeypatch.setattr(application.state.database, "sessions", domain_sessions)
    return application


@pytest.fixture
def domain_client(domain_application):
    with TestClient(domain_application, raise_server_exceptions=False) as test_client:
        yield test_client
