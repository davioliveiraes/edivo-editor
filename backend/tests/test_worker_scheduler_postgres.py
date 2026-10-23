"""Opt-in real PostgreSQL FIFO/claim tests using new synthetic projects only.

These tests prove database scheduling, not FFmpeg media output. The production
worker must be stopped by the coordinating operational QA before execution.
"""

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
from uuid import uuid4

import pytest
from sqlalchemy import select, update
from test_render_postgres import queued, render_pg  # noqa: F401

from app.core.config import Settings
from app.models.preview_job import PreviewJob
from app.models.project import utc_now
from app.models.render_job import RenderJob
from app.repositories.preview_jobs import PreviewClaim, database_now
from app.repositories.render_jobs import RenderClaim
from app.worker.scheduler import WorkerScheduler
from app.worker.supervisor import ProcessStopped

pytestmark = pytest.mark.skipif(
    os.environ.get("EDIVO_QA_WORKER_POSTGRES") != "1",
    reason="requires explicitly coordinated real PostgreSQL R05 scheduler QA",
)


@pytest.fixture
def scheduled(render_pg):  # noqa: F811
    sessions, project_id, *_ = render_pg
    settings = Settings(_env_file=None, database_url=os.environ["EDIVO_QA_DATABASE_URL"])
    worker = WorkerScheduler(
        settings,
        sessions,
        Event(),
        lambda *_: pytest.fail("database-only test must not run a compositor"),
        lambda *_: pytest.fail("database-only test must not run a compositor"),
        project_id=project_id,
    )
    try:
        yield render_pg, worker
    finally:
        with sessions() as session, session.begin():
            session.execute(
                update(PreviewJob)
                .where(
                    PreviewJob.project_id == project_id,
                    PreviewJob.status.in_(("queued", "running")),
                )
                .values(
                    status="failed",
                    stage="failed",
                    claim_token=None,
                    lease_expires_at=None,
                    finished_at=database_now(),
                    error_code="preview_qa_finished",
                )
            )


def add_preview(context, *, created_at=None):
    sessions, project_id, revision_id, *_ = context
    with sessions() as session, session.begin():
        preview = PreviewJob(
            project_id=project_id,
            edit_spec_revision_id=revision_id,
            preview_start_ms=0,
            duration_ms=1000,
            idempotency_key=hashlib.sha256(uuid4().bytes).hexdigest(),
            created_at=created_at or utc_now(),
        )
        session.add(preview)
        session.flush()
        return preview.id


@pytest.mark.parametrize("older", ["preview", "render"])
def test_real_fifo_selects_oldest_across_both_queues(scheduled, older):
    context, worker = scheduled
    render_id = queued(context)
    preview_id = add_preview(
        context, created_at=utc_now() + timedelta(seconds=-2 if older == "preview" else 2)
    )
    owner = worker.claim_next()
    assert owner.id == (preview_id if older == "preview" else render_id)
    assert isinstance(owner, PreviewClaim if older == "preview" else RenderClaim)
    with context[0]() as session:
        preview = session.get(PreviewJob, preview_id)
        render = session.get(RenderJob, render_id)
        assert preview.status == ("running" if older == "preview" else "queued")
        assert render.status == ("running" if older == "render" else "queued")


def test_real_locked_preview_candidate_does_not_block_eligible_render(scheduled):
    context, worker = scheduled
    render_id = queued(context)
    preview_id = add_preview(context, created_at=utc_now() - timedelta(seconds=2))
    with context[0]() as locked, locked.begin():
        locked.scalar(select(PreviewJob).where(PreviewJob.id == preview_id).with_for_update())
        claim = worker.claim_next()
        assert isinstance(claim, RenderClaim) and claim.id == render_id
    with context[0]() as session:
        assert session.get(PreviewJob, preview_id).status == "queued"


def test_real_two_schedulers_claim_each_job_at_most_once(scheduled):
    context, first_worker = scheduled
    render_id = queued(context)
    preview_id = add_preview(context, created_at=utc_now() - timedelta(seconds=2))
    second_worker = WorkerScheduler(
        first_worker.settings,
        context[0],
        Event(),
        lambda *_: None,
        lambda *_: None,
        project_id=context[1],
    )
    barrier = Barrier(2)

    def candidate(worker):
        barrier.wait(timeout=5)
        return worker.claim_next()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(candidate, worker) for worker in (first_worker, second_worker)]
        owners = [future.result(timeout=10) for future in futures]
    owners = [owner for owner in owners if owner is not None]
    # A transaction can briefly lock both queue candidates; the other worker
    # skips those and obtains the remaining job on its following poll.
    if len(owners) < 2:
        owners.append(second_worker.claim_next())
    assert len(owners) == 2 and {owner.id for owner in owners} == {render_id, preview_id}
    assert all(owner.attempts == 1 for owner in owners)
    assert first_worker.claim_next() is None and second_worker.claim_next() is None


def test_real_processor_observes_claim_only_after_commit(scheduled):
    context, worker = scheduled
    render_id = queued(context)
    observed = []

    def processor(owner, _control):
        with context[0]() as verification:
            job = verification.get(RenderJob, owner.id)
            assert job.status == "running" and job.claim_token == owner.claim_token
            assert job.attempts == 1
        observed.append(owner.id)
        # This database scheduling test starts no FFmpeg subprocess.
        raise ProcessStopped()

    worker.renders.processor = processor
    assert worker.run_one()
    assert observed == [render_id]
    with context[0]() as session:
        assert session.get(RenderJob, render_id).status == "queued"


def test_real_shared_recovery_fences_expired_owner_and_exhausts_retry(scheduled):
    context, worker = scheduled
    identifier = queued(context)
    first = worker.claim_next()
    assert isinstance(first, RenderClaim)
    with context[0]() as session, session.begin():
        session.execute(
            update(RenderJob)
            .where(RenderJob.id == identifier)
            .values(lease_expires_at=database_now() - timedelta(seconds=1))
        )
    second = worker.claim_next()
    assert second.id == first.id and second.attempts == 2
    assert second.claim_token != first.claim_token
    with context[0]() as session, session.begin():
        session.execute(
            update(RenderJob)
            .where(RenderJob.id == identifier)
            .values(lease_expires_at=database_now() - timedelta(seconds=1))
        )
    assert worker.claim_next() is None
    with context[0]() as session:
        job = session.get(RenderJob, identifier)
        assert job.status == "failed" and job.error_code == "render_attempts_exhausted"


def test_real_expired_cancellation_is_terminal_and_never_requeued(scheduled):
    context, worker = scheduled
    identifier = queued(context)
    owner = worker.claim_next()
    response = context[5].post(f"{context[3]}/{identifier}/cancel")
    assert response.status_code == 202
    with context[0]() as session, session.begin():
        session.execute(
            update(RenderJob)
            .where(RenderJob.id == identifier)
            .values(lease_expires_at=database_now() - timedelta(seconds=1))
        )
    assert worker.claim_next() is None
    with context[0]() as session:
        job = session.get(RenderJob, identifier)
        assert job.status == "canceled" and job.attempts == owner.attempts
        assert job.claim_token is None and job.artifact_storage_key is None
