"""Relational checks only; PostgreSQL claim/lease behavior is validated separately."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from test_edit_spec import plan, seed_source

from app.models.edit_spec_revision import EditSpecRevision
from app.models.preview_job import PreviewJob


@pytest.fixture
def preview_context(domain_client, domain_sessions):
    project = domain_client.post("/api/v1/projects", json={"name": "Preview DB"}).json()["id"]
    source = seed_source(domain_sessions, project)
    with domain_sessions() as session:
        revision = EditSpecRevision(
            project_id=UUID(project),
            source_media_id=source,
            revision_number=1,
            schema_version=1,
            spec_json={k: v for k, v in plan(source).items() if k != "expected_revision"},
        )
        session.add(revision)
        session.commit()
        return UUID(project), revision.id


def make_job(context, **overrides):
    values = dict(
        project_id=context[0],
        edit_spec_revision_id=context[1],
        preview_start_ms=0,
        duration_ms=1000,
        profile_version=1,
        idempotency_key="a" * 64,
        status="queued",
        stage="queued",
        attempts=0,
    )
    values.update(overrides)
    return PreviewJob(**values)


@pytest.mark.parametrize(
    "values",
    [
        {"duration_ms": 0},
        {"duration_ms": 8001},
        {"preview_start_ms": -1},
        {"attempts": 3},
        {"attempts": -1},
        {"status": "unknown"},
        {"stage": "encoding"},
        {"status": "running", "stage": "preparing"},
        {"status": "completed", "stage": "completed"},
        {"status": "failed", "stage": "encoding"},
        {"profile_version": 0},
        {"idempotency_key": "short"},
        {"artifact_byte_size": 0},
        {"artifact_sha256": "short"},
        {"project_id": uuid4()},
        {"edit_spec_revision_id": uuid4()},
    ],
)
def test_job_database_rejects_invalid_state(domain_sessions, preview_context, values):
    with domain_sessions() as session:
        session.add(make_job(preview_context, **values))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_running_requires_fencing_fields_and_completed_metadata(domain_sessions, preview_context):
    now = datetime.now(UTC)
    with domain_sessions() as session:
        job = make_job(
            preview_context,
            status="running",
            stage="preparing",
            attempts=1,
            claim_token=uuid4(),
            heartbeat_at=now,
            lease_expires_at=now + timedelta(seconds=30),
        )
        session.add(job)
        session.commit()
        job.stage = "validating"
        session.commit()
        job.status = "completed"
        job.stage = "completed"
        job.artifact_storage_key = f"previews/{preview_context[0]}/{job.id}/preview.mp4"
        job.artifact_sha256 = "f" * 64
        job.artifact_byte_size = 100
        job.finished_at = now
        session.commit()


@pytest.mark.parametrize("existing_status", ["queued", "running", "completed"])
def test_live_request_unique_but_failed_explicit_retry_allowed(
    domain_sessions, preview_context, existing_status
):
    now = datetime.now(UTC)
    extra = {}
    if existing_status == "running":
        extra = dict(
            attempts=1,
            claim_token=uuid4(),
            heartbeat_at=now,
            lease_expires_at=now + timedelta(seconds=30),
        )
    elif existing_status == "completed":
        extra = dict(
            artifact_storage_key="previews/result.mp4",
            artifact_sha256="b" * 64,
            artifact_byte_size=100,
            finished_at=now,
        )
    with domain_sessions() as session:
        existing = make_job(
            preview_context,
            status=existing_status,
            stage="preparing" if existing_status == "running" else existing_status,
            **extra,
        )
        session.add(existing)
        session.commit()
        session.add(make_job(preview_context))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        existing.status = "failed"
        existing.stage = "failed"
        existing.finished_at = now
        session.commit()
        retry = make_job(preview_context)
        session.add(retry)
        session.commit()
        assert retry.id != existing.id
