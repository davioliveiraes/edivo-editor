"""Real PostgreSQL cancellation/publication races with fully encoded MP4.

Opt-in only after root builds the worker sources and stops the application
worker: EDIVO_QA_RENDER_POSTGRES=1, private EDIVO_QA_DATABASE_URL alias. The
shared fixture creates its own synthetic project/source through HTTP. Barriers
control publication timing, never replace encoding, validation or PG commits.
"""

import hashlib
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import sleep
from uuid import UUID

import pytest
from sqlalchemy import select
from test_render_postgres import queued
from test_render_postgres import render_pg as render_pg

from app.core.config import Settings
from app.models.media_asset import MediaAsset
from app.models.render_job import RenderJob
from app.storage.local import LocalStorage
from app.worker.render_processor import RenderProcessor
from app.worker.scheduler import WorkerScheduler

pytestmark = pytest.mark.skipif(
    os.environ.get("EDIVO_QA_RENDER_POSTGRES") != "1",
    reason="requires explicit real PostgreSQL/API R05 operational QA",
)


def settings_for_runtime():
    # conftest intentionally resets EDIVO_DATABASE_URL, so only this private
    # explicit alias may point to the real database. It is never logged.
    return Settings(_env_file=None, database_url=os.environ["EDIVO_QA_DATABASE_URL"])


def scheduler_for(context, settings, processor):
    sessions, project_id, *_ = context

    def unexpected_preview(*_args):
        pytest.fail("this isolated project has no preview jobs")

    return WorkerScheduler(
        settings, sessions, Event(), unexpected_preview, processor, project_id=project_id
    )


def source_snapshot(context, settings):
    sessions, project_id, *_ = context
    with sessions() as session:
        media = session.scalar(select(MediaAsset).where(MediaAsset.project_id == project_id))
        opened = LocalStorage(settings).open_original(
            media.storage_key, project_id, media.id, media.byte_size
        )
        with opened.file as source:
            digest = hashlib.sha256()
            while chunk := source.read(65536):
                digest.update(chunk)
            metadata = os.fstat(source.fileno())
        assert digest.hexdigest() == media.checksum_sha256
        assert stat.S_IMODE(metadata.st_mode) == 0o400
        return digest.hexdigest(), metadata.st_mtime_ns, metadata.st_mode, metadata.st_size


def assert_no_current_workspace(settings, job_id):
    temporary = settings.storage_root / "temp"
    if temporary.exists():
        assert not list(temporary.glob(f"render-{job_id}-*"))


def assert_real_completed_artifact(context, settings, job_id):
    sessions, project_id, *_ = context
    with sessions() as session:
        job = session.get(RenderJob, job_id)
        assert job.status == job.stage == "completed"
        assert job.claim_token is None and job.lease_expires_at is None
        assert job.cancel_requested_at is None
        assert job.artifact_duration_ms == 1000
        assert (job.artifact_width, job.artifact_height) == (1080, 1920)
        assert (job.artifact_fps_num, job.artifact_fps_den) == (30, 1)
        opened = LocalStorage(settings).open_render(
            job.artifact_storage_key, project_id, job_id, job.artifact_byte_size
        )
        with opened.file as output:
            digest = hashlib.sha256()
            while chunk := output.read(65536):
                digest.update(chunk)
            assert stat.S_IMODE(os.fstat(output.fileno()).st_mode) == 0o400
        assert digest.hexdigest() == job.artifact_sha256


def test_real_cancel_committed_after_validation_before_publication_prevents_completed(render_pg):
    settings = settings_for_runtime()
    sessions, project_id, _revision_id, base, _payload, client = render_pg
    before = source_snapshot(render_pg, settings)
    job_id = queued(render_pg)
    ready, release = Event(), Event()

    class DelayedPublication(RenderProcessor):
        def publish(self, claim, staged, metadata, runner, control):
            # This callback is reached only after genuine FFmpeg encoding, full
            # decoding and SHA verification, and before acquiring Project lock.
            assert metadata["duration_ms"] == 1000
            assert staged.path.is_file() and staged.byte_size > 0
            ready.set()
            assert release.wait(timeout=15), "qa_publication_barrier_not_released"
            return super().publish(claim, staged, metadata, runner, control)

    processor = DelayedPublication(settings, sessions, Event())
    worker = scheduler_for(render_pg, settings, processor)
    with ThreadPoolExecutor(max_workers=1) as executor:
        processing = executor.submit(worker.run_one)
        try:
            assert ready.wait(timeout=45), "real_encoding_did_not_reach_publication"
            cancellation = client.post(f"{base}/{job_id}/cancel")
            assert cancellation.status_code == 202
            assert cancellation.json()["cancel_requested_at"] is not None
            assert cancellation.json()["status"] == "running"
        finally:
            release.set()
        assert processing.result(timeout=30) is True
    response = client.get(f"{base}/{job_id}")
    assert response.status_code == 200
    state = response.json()
    assert state["status"] == state["stage"] == "canceled"
    assert state["artifact_available"] is False and state["file_url"] is None
    assert state["byte_size"] is None and state["checksum_sha256"] is None
    assert client.get(f"{base}/{job_id}/file").status_code == 409
    with sessions() as session:
        job = session.get(RenderJob, job_id)
        assert job.artifact_storage_key is None
        assert job.claim_token is None and job.lease_expires_at is None
    assert not (settings.storage_root / "renders" / str(project_id) / str(job_id)).exists()
    assert_no_current_workspace(settings, job_id)
    assert source_snapshot(render_pg, settings) == before


def test_real_completed_commit_wins_cancel_waiting_on_publication_lock(render_pg, monkeypatch):
    settings = settings_for_runtime()
    sessions, _project_id, _revision_id, base, _payload, client = render_pg
    before = source_snapshot(render_pg, settings)
    job_id = queued(render_pg)
    promoted, release = Event(), Event()
    real_promote = LocalStorage.promote_render

    def paused_after_real_promotion(storage, staged, project_id, render_id):
        # This is the production no-replace publication operation. The worker
        # holds Project -> Job locks while this barrier delays completion.
        key = real_promote(storage, staged, project_id, render_id)
        assert render_id == job_id
        promoted.set()
        assert release.wait(timeout=15), "qa_promoted_barrier_not_released"
        return key

    monkeypatch.setattr(LocalStorage, "promote_render", paused_after_real_promotion)
    processor = RenderProcessor(settings, sessions, Event())
    worker = scheduler_for(render_pg, settings, processor)
    with ThreadPoolExecutor(max_workers=2) as executor:
        processing = executor.submit(worker.run_one)
        cancellation = None
        try:
            assert promoted.wait(timeout=45), "real_encoding_did_not_publish"
            cancellation = executor.submit(client.post, f"{base}/{job_id}/cancel")
            sleep(0.15)
            assert not cancellation.done(), "cancel_should_wait_for_project_lock"
        finally:
            release.set()
        assert processing.result(timeout=30) is True
        assert cancellation is not None
        response = cancellation.result(timeout=20)
    assert response.status_code == 409
    assert response.json()["code"] == "render_not_cancelable"
    state = client.get(f"{base}/{job_id}").json()
    assert UUID(state["id"]) == job_id
    assert state["status"] == "completed" and state["artifact_available"]
    assert state["progress_percent"] == 100 and state["cancel_requested_at"] is None
    file_response = client.get(f"{base}/{job_id}/file")
    assert file_response.status_code == 200
    assert hashlib.sha256(file_response.content).hexdigest() == state["checksum_sha256"]
    assert_real_completed_artifact(render_pg, settings, job_id)
    assert_no_current_workspace(settings, job_id)
    assert source_snapshot(render_pg, settings) == before
