"""Filesystem/transaction compensation units with an explicitly injected renderer.

Synthetic bytes here are not represented as FFmpeg validation evidence. The
production renderer and PostgreSQL/concurrency checks run in separate suites.
"""

import hashlib
import io
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from test_edit_spec import plan, seed_source

from app.models.audio_asset import AudioAsset
from app.models.edit_spec_revision import EditSpecRevision
from app.models.media_asset import MediaAsset
from app.models.project import Project
from app.models.render_job import RenderJob
from app.repositories.render_jobs import RenderClaim, RenderJobRepository
from app.storage.local import LocalStorage
from app.worker import render_processor as module
from app.worker.render_processor import RenderProcessor
from app.worker.supervisor import LeaseLost, PreviewProcessError, RenderCanceled


@pytest.fixture
def processing(settings, domain_sessions, monkeypatch):
    settings = settings.model_copy(
        update={"storage_min_free_bytes": 0, "render_max_output_bytes": 1024 * 1024}
    )
    with domain_sessions() as session, session.begin():
        project = Project(name="Render processor unit")
        session.add(project)
        session.flush()
        project_id = project.id
    source_id = seed_source(domain_sessions, project_id, duration_ms=14000)
    storage = LocalStorage(settings)
    staged = storage.stage(io.BytesIO(b"source bytes for isolated render processor checks"))
    key = storage.promote(staged, project_id, source_id)
    source_path = storage.root / key
    with domain_sessions() as session, session.begin():
        media = session.get(MediaAsset, source_id)
        media.byte_size, media.checksum_sha256 = staged.byte_size, staged.checksum_sha256
        media.probe_metadata = {"video_stream_index": 0}
        revision = EditSpecRevision(
            project_id=project_id,
            source_media_id=source_id,
            revision_number=1,
            schema_version=1,
            spec_json={
                k: v
                for k, v in plan(source_id, start_ms=1000, end_ms=13000).items()
                if k != "expected_revision"
            },
        )
        session.add(revision)
        session.flush()
        job = RenderJob(
            project_id=project_id,
            edit_spec_revision_id=revision.id,
            profile="standard",
            render_engine_version="r05-1",
            request_fingerprint="a" * 64,
            status="running",
            stage="preparing",
            attempts=1,
            claim_token=uuid4(),
            heartbeat_at=datetime.now(UTC),
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        )
        session.add(job)
        session.flush()
        owner = RenderClaim(
            job.id, project_id, revision.id, job.claim_token, 1, 2, "standard", "r05-1"
        )

    class Repository(RenderJobRepository):
        def control_state(self, claim):
            job = self.session.get(RenderJob, claim.id)
            if (
                job is None
                or job.status != "running"
                or job.claim_token != claim.claim_token
                or job.attempts != claim.attempts
            ):
                return "lost"
            return "cancel_requested" if job.cancel_requested_at is not None else "owned"

        def lock_owned_for_publish(self, claim):
            return (
                self.session.get(RenderJob, claim.id)
                if self.control_state(claim) == "owned"
                else None
            )

    monkeypatch.setattr(module, "RenderJobRepository", Repository)
    monkeypatch.setattr(module, "database_now", lambda: datetime.now(UTC))
    stages, progress, observed = [], [], []
    control = SimpleNamespace(
        heartbeat=lambda: True,
        control=lambda: None,
        set_stage=stages.append,
        set_progress=progress.append,
    )

    def renderer(_settings, saved_plan, *, workspace, set_stage, set_progress, **kwargs):
        observed.append(saved_plan.model_dump(mode="json"))
        assert kwargs["profile"] == "standard"
        assert os.read(kwargs["source_fd"], 4096) == source_path.read_bytes()
        target = workspace / "final.part"
        target.write_bytes(b"unit artifact; never advertised as a real encoded MP4")
        set_stage("encoding")
        set_progress(11000)
        set_stage("validating")
        expected_audio = kwargs["source_has_audio"] or kwargs["background_fd"] is not None
        return SimpleNamespace(
            path=target,
            byte_size=target.stat().st_size,
            checksum_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
            metadata={
                "duration_ms": 12000,
                "width": 1080,
                "height": 1920,
                "fps_num": 30,
                "fps_den": 1,
                "video_codec": "h264",
                "pixel_format": "yuv420p",
                "sample_aspect_ratio": "1:1",
                "has_audio": expected_audio,
                "audio_codec": "aac" if expected_audio else None,
            },
        )

    return SimpleNamespace(
        settings=settings,
        sessions=domain_sessions,
        owner=owner,
        control=control,
        renderer=renderer,
        source_path=source_path,
        source_id=source_id,
        storage=storage,
        stages=stages,
        progress=progress,
        observed=observed,
        canonical=storage.root / f"renders/{project_id}/{owner.id}/final.mp4",
        repository=Repository,
    )


def process(context, renderer=None):
    RenderProcessor(
        context.settings, context.sessions, Event(), renderer=renderer or context.renderer
    )(context.owner, context.control)


def assert_unpublished(context):
    assert not context.canonical.exists()
    assert not list((context.storage.root / "temp").iterdir())
    with context.sessions() as session:
        assert session.get(RenderJob, context.owner.id).status == "running"


def test_saved_full_revision_published_with_metadata_after_confirmed_commit(
    processing, monkeypatch
):
    context = processing
    original = context.source_path.read_bytes()
    events = []

    def completed(message, *, extra):
        with context.sessions() as session:
            assert session.get(RenderJob, context.owner.id).status == "completed"
        assert message == "render_job_completed" and extra["stage"] == "completed"
        events.append(message)

    monkeypatch.setattr(module.logger, "info", completed)
    process(context)
    with context.sessions() as session:
        job = session.get(RenderJob, context.owner.id)
        assert job.status == job.stage == "completed"
        assert job.edit_spec_revision_id == context.owner.edit_spec_revision_id
        assert job.artifact_sha256 == hashlib.sha256(context.canonical.read_bytes()).hexdigest()
        assert job.artifact_byte_size == context.canonical.stat().st_size
        assert job.artifact_duration_ms == 12000
        assert (job.artifact_width, job.artifact_height) == (1080, 1920)
        assert (job.artifact_video_codec, job.artifact_audio_codec) == ("h264", None)
        assert job.artifact_fps_num == 30 and job.claim_token is None
        revision = session.get(EditSpecRevision, context.owner.edit_spec_revision_id)
        assert revision.schema_version == 1 and "audio" not in revision.spec_json
    assert context.canonical.stat().st_mode & 0o777 == 0o400
    assert context.source_path.read_bytes() == original
    assert context.source_path.stat().st_mode & 0o777 == 0o400
    assert context.stages == ["encoding", "validating", "publishing"]
    assert context.progress == [11000]
    assert context.observed[0]["trim"] == {"start_ms": 1000, "end_ms": 13000}
    assert events == ["render_job_completed"]
    assert not list((context.storage.root / "temp").iterdir())


def test_existing_canonical_is_never_overwritten_or_adopted(processing):
    context = processing
    context.canonical.parent.mkdir(parents=True, mode=0o700)
    context.canonical.write_bytes(b"prior bytes requiring read-only reconciliation")
    context.canonical.chmod(0o400)
    with pytest.raises(PreviewProcessError, match="render_publication_interrupted"):
        process(context)
    assert context.canonical.read_bytes() == b"prior bytes requiring read-only reconciliation"
    assert not list((context.storage.root / "temp").iterdir())


@pytest.mark.parametrize("fault", ["checksum", "permissions", "absent", "symlink"])
def test_invalid_source_cannot_start_renderer(processing, tmp_path, fault):
    context = processing
    if fault == "permissions":
        context.source_path.chmod(0o600)
    elif fault == "checksum":
        context.source_path.chmod(0o600)
        context.source_path.write_bytes(b"x" * context.source_path.stat().st_size)
        context.source_path.chmod(0o400)
    else:
        context.source_path.unlink()
        if fault == "symlink":
            outside = tmp_path / "unmanaged-source.mp4"
            outside.write_bytes(b"outside")
            context.source_path.symlink_to(outside)

    def renderer(*_args, **_kwargs):
        pytest.fail("invalid source cannot reach encoder")

    with pytest.raises(PreviewProcessError):
        process(context, renderer)
    assert_unpublished(context)


@pytest.mark.parametrize(
    "fault",
    [
        "checksum",
        "external_path",
        "symlink",
        "fifo",
        "encoding_failure",
        "width",
        "duration",
        "codec",
    ],
)
def test_invalid_artifact_is_not_published_and_attempt_temporary_files_are_cleaned(
    processing, tmp_path, fault
):
    context = processing
    original = context.source_path.read_bytes()

    def renderer(*args, **kwargs):
        artifact = context.renderer(*args, **kwargs)
        if fault == "encoding_failure":
            raise PreviewProcessError("render_encoding_failed")
        if fault == "checksum":
            artifact.checksum_sha256 = "0" * 64
        elif fault == "external_path":
            artifact.path = tmp_path / "unmanaged.mp4"
        elif fault == "symlink":
            outside = tmp_path / "unmanaged.mp4"
            outside.write_bytes(artifact.path.read_bytes())
            artifact.path.unlink()
            artifact.path.symlink_to(outside)
        elif fault == "fifo":
            artifact.path.unlink()
            os.mkfifo(artifact.path, mode=0o600)
        elif fault == "width":
            artifact.metadata["width"] = 540
        elif fault == "duration":
            artifact.metadata["duration_ms"] = 8000
        elif fault == "codec":
            artifact.metadata["video_codec"] = "mpeg4"
        return artifact

    with pytest.raises(PreviewProcessError):
        process(context, renderer)
    assert_unpublished(context)
    assert context.source_path.read_bytes() == original


def test_disk_full_prevents_encoding_without_cleaning_other_files(processing, monkeypatch):
    context = processing
    unknown = context.storage.root / "unrelated-keep.txt"
    unknown.write_text("preserve")
    monkeypatch.setattr(
        "app.storage.local.shutil.disk_usage", lambda _root: SimpleNamespace(free=0)
    )

    def renderer(*_args, **_kwargs):
        pytest.fail("no room for configured output budget")

    with pytest.raises(PreviewProcessError, match="render_storage_unavailable"):
        process(context, renderer)
    assert_unpublished(context)
    assert unknown.read_text() == "preserve"


def test_cancel_persisted_before_publication_prevents_completion(processing):
    context = processing

    def renderer(*args, **kwargs):
        artifact = context.renderer(*args, **kwargs)
        with context.sessions() as session, session.begin():
            session.get(RenderJob, context.owner.id).cancel_requested_at = datetime.now(UTC)
        return artifact

    with pytest.raises(RenderCanceled):
        process(context, renderer)
    assert_unpublished(context)


def test_lost_lease_after_link_preserves_canonical_without_completing(processing, monkeypatch):
    context = processing
    checks = []
    original = context.repository.control_state

    def state(self, claim):
        checks.append(True)
        return "lost" if len(checks) >= 3 else original(self, claim)

    monkeypatch.setattr(context.repository, "control_state", state)
    with pytest.raises(LeaseLost):
        process(context)
    assert context.canonical.exists()
    with context.sessions() as session:
        assert session.get(RenderJob, context.owner.id).status == "running"
    assert not list((context.storage.root / "temp").iterdir())


def test_uncertain_commit_preserves_committed_artifact_without_success_log(processing, monkeypatch):
    context = processing
    events = []
    monkeypatch.setattr(module.logger, "info", lambda *args, **kwargs: events.append(args))

    def lost_reply(_session):
        raise OperationalError("commit", "hidden", RuntimeError("connection_lost"))

    event.listen(context.sessions.class_, "after_commit", lost_reply)
    try:
        with pytest.raises(PreviewProcessError, match="render_database_unavailable"):
            process(context)
    finally:
        event.remove(context.sessions.class_, "after_commit", lost_reply)
    assert context.canonical.exists() and context.canonical.stat().st_mode & 0o777 == 0o400
    with context.sessions() as session:
        assert session.get(RenderJob, context.owner.id).status == "completed"
    assert events == []


@pytest.mark.parametrize("fault", ["profile", "engine", "revision", "stream", "deleted"])
def test_incompatible_job_or_inputs_fail_before_encoding(processing, fault):
    context = processing
    with context.sessions() as session, session.begin():
        job = session.get(RenderJob, context.owner.id)
        if fault == "profile":
            job.profile = "high"
        elif fault == "engine":
            job.render_engine_version = "incompatible"
        elif fault == "revision":
            context.owner = replace(context.owner, edit_spec_revision_id=uuid4())
        elif fault == "stream":
            session.get(MediaAsset, context.source_id).probe_metadata = {"video_stream_index": True}
        else:
            session.get(Project, context.owner.project_id).deleted_at = datetime.now(UTC)

    def renderer(*_args, **_kwargs):
        pytest.fail("incompatible job cannot reach compositor")

    with pytest.raises(PreviewProcessError):
        process(context, renderer)
    assert not context.canonical.exists()


def test_v2_audio_reuses_separate_inventory_and_preserves_immutable_snapshot(processing):
    context = processing
    audio_id = uuid4()
    staged = context.storage.stage_audio(io.BytesIO(b"audio bytes for unit-only checksum checks"))
    key = context.storage.promote_audio(staged, context.owner.project_id, audio_id)
    with context.sessions() as session, session.begin():
        audio = AudioAsset(
            id=audio_id,
            project_id=context.owner.project_id,
            original_filename="synthetic.mp3",
            storage_key=key,
            checksum_sha256=staged.checksum_sha256,
            byte_size=staged.byte_size,
            mime_detected="audio/mpeg",
            duration_ms=1000,
            codec="mp3",
            sample_rate=48000,
            channels=2,
        )
        session.add(audio)
        session.flush()
        previous = session.get(EditSpecRevision, context.owner.edit_spec_revision_id)
        payload = previous.spec_json | {
            "schema_version": 2,
            "text_overlays": [],
            "audio": {
                "source_volume": 0.5,
                "background_audio_id": str(audio_id),
                "background_volume": 0.15,
                "background_loop": True,
                "fade_in_ms": 300,
                "fade_out_ms": 600,
            },
        }
        revision = EditSpecRevision(
            project_id=context.owner.project_id,
            source_media_id=context.source_id,
            revision_number=2,
            schema_version=2,
            spec_json=payload,
        )
        session.add(revision)
        session.flush()
        session.get(RenderJob, context.owner.id).edit_spec_revision_id = revision.id
        context.owner = replace(context.owner, edit_spec_revision_id=revision.id)
    process(context)
    with context.sessions() as session:
        job = session.get(RenderJob, context.owner.id)
        assert job.artifact_audio_codec == "aac"
        assert (
            session.get(EditSpecRevision, context.owner.edit_spec_revision_id).spec_json == payload
        )
    assert context.observed[0]["audio"] == payload["audio"]
    assert (context.storage.root / key).stat().st_mode & 0o777 == 0o400
