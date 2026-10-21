"""Processor compensation units use synthetic bytes and an injected test renderer.

These checks do not claim FFmpeg composition or PostgreSQL concurrency evidence.
"""

import hashlib
import io
import os
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from test_edit_spec import plan, seed_source

from app.models.edit_spec_revision import EditSpecRevision
from app.models.media_asset import MediaAsset
from app.models.preview_job import PreviewJob
from app.repositories.preview_jobs import PreviewClaim, PreviewJobRepository
from app.storage.local import LocalStorage
from app.worker import processor as module
from app.worker.processor import PreviewProcessor
from app.worker.supervisor import LeaseLost, PreviewProcessError


@pytest.fixture
def processing(settings, domain_client, domain_sessions, monkeypatch):
    # Unit artifacts contain only a few bytes. A full production reserve would
    # require 128 MiB inside the container's 128 MiB tmpfs before writing anything.
    settings = settings.model_copy(
        update={"storage_min_free_bytes": 0, "preview_max_bytes": 1024 * 1024}
    )
    project = UUID(
        domain_client.post("/api/v1/projects", json={"name": "Processor unit"}).json()["id"]
    )
    source_id = seed_source(domain_sessions, project)
    storage = LocalStorage(settings)
    staged = storage.stage(io.BytesIO(b"synthetic source bytes for unit checks"))
    key = storage.promote(staged, project, source_id)
    source_path = storage.root / key
    with domain_sessions() as session, session.begin():
        media = session.get(MediaAsset, source_id)
        media.byte_size, media.checksum_sha256 = staged.byte_size, staged.checksum_sha256
        media.probe_metadata = {"video_stream_index": 0}
        revision = EditSpecRevision(
            project_id=project,
            source_media_id=source_id,
            revision_number=1,
            schema_version=1,
            spec_json={k: v for k, v in plan(source_id).items() if k != "expected_revision"},
        )
        session.add(revision)
        session.flush()
        job = PreviewJob(
            project_id=project,
            edit_spec_revision_id=revision.id,
            preview_start_ms=0,
            duration_ms=1000,
            idempotency_key="a" * 64,
            status="running",
            stage="preparing",
            attempts=1,
            claim_token=uuid4(),
            heartbeat_at=datetime.now(UTC),
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        )
        session.add(job)
        session.flush()
        owner = PreviewClaim(job.id, project, revision.id, job.claim_token, 1, 0, 1000, 1)

    class Repository(PreviewJobRepository):
        def is_owned(self, claim):
            job = self.session.get(PreviewJob, claim.id)
            return bool(job and job.status == "running" and job.claim_token == claim.claim_token)

        def lock_owned_for_publish(self, claim):
            return self.session.get(PreviewJob, claim.id) if self.is_owned(claim) else None

    monkeypatch.setattr(module, "PreviewJobRepository", Repository)
    monkeypatch.setattr(module, "database_now", lambda: datetime.now(UTC))
    stages = []
    control = SimpleNamespace(heartbeat=lambda: True, set_stage=stages.append)

    def renderer(_settings, _plan, *, workspace, set_stage, **kwargs):
        set_stage("encoding")
        assert os.read(kwargs["source_fd"], 4096) == source_path.read_bytes()
        target = workspace / "preview.part"
        target.write_bytes(b"unit artifact bytes; not an encoded MP4")
        set_stage("validating")
        return SimpleNamespace(
            path=target,
            byte_size=target.stat().st_size,
            checksum_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
        )

    return SimpleNamespace(
        settings=settings,
        sessions=domain_sessions,
        owner=owner,
        control=control,
        renderer=renderer,
        source_path=source_path,
        storage=storage,
        stages=stages,
        canonical=storage.root / f"previews/{project}/{owner.id}/preview.mp4",
        repository=Repository,
    )


def process(context, renderer=None):
    PreviewProcessor(
        context.settings, context.sessions, Event(), renderer=renderer or context.renderer
    )(context.owner, context.control)


def test_verified_artifact_published_only_after_validation(processing, monkeypatch):
    original = processing.source_path.read_bytes()
    observed = []

    def committed_event(message, *, extra):
        assert message == "preview_job_completed"
        assert extra["stage"] == "completed"
        with processing.sessions() as verification:
            assert verification.get(PreviewJob, processing.owner.id).status == "completed"
        observed.append(message)

    monkeypatch.setattr(module.logger, "info", committed_event)
    process(processing)
    with processing.sessions() as session:
        job = session.get(PreviewJob, processing.owner.id)
        assert job.status == job.stage == "completed"
        assert job.artifact_sha256 == hashlib.sha256(processing.canonical.read_bytes()).hexdigest()
        assert job.artifact_byte_size == processing.canonical.stat().st_size
    assert processing.canonical.stat().st_mode & 0o777 == 0o400
    assert processing.source_path.read_bytes() == original
    assert processing.source_path.stat().st_mode & 0o777 == 0o400
    assert processing.stages == ["encoding", "validating", "publishing"]
    assert observed == ["preview_job_completed"]
    assert not list((processing.storage.root / "temp").iterdir())


def test_existing_canonical_is_preserved_without_adoption(processing):
    processing.canonical.parent.mkdir(parents=True, mode=0o700)
    processing.canonical.write_bytes(b"previous canonical")
    processing.canonical.chmod(0o400)
    with pytest.raises(PreviewProcessError, match="preview_publication_interrupted"):
        process(processing)
    assert processing.canonical.read_bytes() == b"previous canonical"
    with processing.sessions() as session:
        assert session.get(PreviewJob, processing.owner.id).status == "running"
    assert not list((processing.storage.root / "temp").iterdir())


def test_no_free_disk_blocks_renderer_and_preserves_source(processing, monkeypatch):
    original = processing.source_path.read_bytes()
    rendered = []
    monkeypatch.setattr(
        "app.storage.local.shutil.disk_usage", lambda _root: SimpleNamespace(free=0)
    )

    def renderer(*_args, **_kwargs):
        rendered.append(True)
        pytest.fail("renderer_must_not_run_without_disk_space")

    with pytest.raises(PreviewProcessError, match="preview_storage_unavailable"):
        process(processing, renderer)
    assert rendered == []
    assert processing.source_path.read_bytes() == original
    assert processing.source_path.stat().st_mode & 0o777 == 0o400
    assert not list((processing.storage.root / "temp").iterdir())
    assert not processing.canonical.exists()


@pytest.mark.parametrize("fault", ["checksum", "permissions", "absent"])
def test_invalid_original_prevents_renderer_and_cleans_workspace(processing, fault):
    processing.source_path.chmod(0o600)
    if fault == "checksum":
        processing.source_path.write_bytes(b"x" * processing.source_path.stat().st_size)
        processing.source_path.chmod(0o400)
    elif fault == "absent":
        processing.source_path.unlink()

    def renderer(*_args, **_kwargs):
        pytest.fail("renderer_must_not_run")

    with pytest.raises(PreviewProcessError):
        process(processing, renderer)
    assert not processing.canonical.exists()
    assert not list((processing.storage.root / "temp").iterdir())


@pytest.mark.parametrize("fault", ["checksum", "external_path", "symlink", "encoding_failure"])
def test_renderer_failures_do_not_publish_or_leave_workspace(processing, tmp_path, fault):
    original = processing.source_path.read_bytes()

    def renderer(*args, **kwargs):
        artifact = processing.renderer(*args, **kwargs)
        if fault == "encoding_failure":
            raise PreviewProcessError("preview_encoding_failed")
        if fault == "checksum":
            artifact.checksum_sha256 = "0" * 64
        elif fault == "external_path":
            artifact.path = tmp_path / "outside.mp4"
        else:
            target = tmp_path / "external.mp4"
            target.write_bytes(artifact.path.read_bytes())
            artifact.path.unlink()
            artifact.path.symlink_to(target)
        return artifact

    with pytest.raises(PreviewProcessError):
        process(processing, renderer)
    assert not processing.canonical.exists()
    assert processing.source_path.read_bytes() == original
    assert not list((processing.storage.root / "temp").iterdir())


def test_lease_lost_after_link_preserves_canonical_and_does_not_complete(processing, monkeypatch):
    checks = []
    original = processing.repository.is_owned

    def is_owned(self, claim):
        checks.append(True)
        return len(checks) < 3 and original(self, claim)

    monkeypatch.setattr(processing.repository, "is_owned", is_owned)
    with pytest.raises(LeaseLost):
        process(processing)
    assert processing.canonical.exists()
    with processing.sessions() as session:
        assert session.get(PreviewJob, processing.owner.id).status == "running"


def test_uncertain_commit_reply_preserves_committed_canonical(processing, monkeypatch):
    events = []
    monkeypatch.setattr(module.logger, "info", lambda *args, **kwargs: events.append(args))

    def lost_reply(_session):
        raise OperationalError("commit", "hidden", RuntimeError("connection_lost"))

    event.listen(processing.sessions.class_, "after_commit", lost_reply)
    try:
        with pytest.raises(PreviewProcessError, match="preview_database_unavailable"):
            process(processing)
    finally:
        event.remove(processing.sessions.class_, "after_commit", lost_reply)
    assert processing.canonical.exists()
    assert processing.canonical.stat().st_mode & 0o777 == 0o400
    with processing.sessions() as session:
        assert session.get(PreviewJob, processing.owner.id).status == "completed"
    assert events == []
