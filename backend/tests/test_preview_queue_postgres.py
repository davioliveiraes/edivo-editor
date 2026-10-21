"""Opt-in real PostgreSQL/HTTP queue tests, scoped to new synthetic projects.

Run in the API container with EDIVO_QA_QUEUE_POSTGRES=1 and
EDIVO_QA_DATABASE_URL copied from its existing EDIVO_DATABASE_URL without
printing it. The standard conftest deliberately replaces EDIVO_DATABASE_URL.
Every fixture is soft-deleted and its jobs terminalized; originals/revisions stay.
"""

import hashlib
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier, Event
from time import sleep
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.models.preview_job import PreviewJob
from app.repositories.preview_jobs import PreviewJobRepository, database_now
from app.worker.preview_worker import PreviewWorker
from app.worker.supervisor import ProcessRunner

pytestmark = pytest.mark.skipif(
    os.environ.get("EDIVO_QA_QUEUE_POSTGRES") != "1",
    reason="requires explicitly enabled real PostgreSQL/API operational QA",
)


@pytest.fixture
def queue_pg(tmp_path):
    actual_url = os.environ.get("EDIVO_QA_DATABASE_URL")
    if not actual_url:
        pytest.fail("real_queue_database_url_required")
    engine = create_engine(actual_url, hide_parameters=True, pool_pre_ping=True)
    assert engine.dialect.name == "postgresql"
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    source = tmp_path / "synthetic.mp4"
    encoded = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "color=c=navy:size=160x90:rate=24:duration=1",
            "-c:v",
            "libx264",
            "-threads",
            "1",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert encoded.returncode == 0
    project_id = None
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=30) as client:
        try:
            response = client.post("/api/v1/projects", json={"name": f"QA R04 queue {uuid4()}"})
            assert response.status_code == 201
            project_id = UUID(response.json()["id"])
            response = client.post(
                f"/api/v1/projects/{project_id}/media",
                files={"file": ("synthetic.mp4", source.read_bytes(), "video/mp4")},
            )
            assert response.status_code == 201
            media_id = response.json()["id"]
            response = client.put(
                f"/api/v1/projects/{project_id}/edit-spec",
                json={
                    "expected_revision": 0,
                    "schema_version": 1,
                    "source_media_id": media_id,
                    "trim": {"start_ms": 0, "end_ms": 1000},
                    "canvas": {"preset": "9:16", "fit": "cover", "focal_x": 0.5, "focal_y": 0.5},
                },
            )
            assert response.status_code == 201
            revision_id = UUID(response.json()["id"])
            configured = Settings(_env_file=None, database_url=actual_url)

            def add_job():
                with sessions() as session, session.begin():
                    job = PreviewJob(
                        project_id=project_id,
                        edit_spec_revision_id=revision_id,
                        preview_start_ms=0,
                        duration_ms=1000,
                        idempotency_key=hashlib.sha256(uuid4().bytes).hexdigest(),
                    )
                    session.add(job)
                    session.flush()
                    return job.id

            yield sessions, project_id, add_job, configured, client
        finally:
            if project_id is not None:
                # All mutation is scoped to this fixture's new project. No fake
                # completed artifact or active job survives the operational test.
                with sessions() as session, session.begin():
                    session.execute(
                        update(PreviewJob)
                        .where(PreviewJob.project_id == project_id)
                        .values(
                            status="failed",
                            stage="failed",
                            claim_token=None,
                            lease_expires_at=None,
                            finished_at=database_now(),
                            error_code="preview_qa_finished",
                        )
                    )
                response = client.delete(f"/api/v1/projects/{project_id}")
                assert response.status_code in (204, 404)
                assert client.get(f"/api/v1/projects/{project_id}").status_code == 404
            engine.dispose()


def claimed(sessions, project, lease=30):
    with sessions() as session, session.begin():
        return PreviewJobRepository(session).claim_next(lease, project_id=project)


def expire(sessions, job_id):
    with sessions() as session, session.begin():
        session.execute(
            update(PreviewJob)
            .where(PreviewJob.id == job_id)
            .values(lease_expires_at=database_now() - timedelta(seconds=1))
        )


def test_atomic_claim_two_candidates_only_one_wins(queue_pg):
    sessions, project, add_job, _settings, _client = queue_pg
    job_id = add_job()
    barrier = Barrier(2)

    def candidate():
        barrier.wait(timeout=5)
        return claimed(sessions, project)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(candidate) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert winners[0].id == job_id
    assert winners[0].attempts == 1
    with sessions() as session:
        assert PreviewJobRepository(session).count_active(project) == 1


def test_skip_locked_does_not_wait_for_first_job(queue_pg):
    sessions, project, add_job, _settings, _client = queue_pg
    first, second = add_job(), add_job()
    with sessions() as locked, locked.begin():
        locked.scalar(select(PreviewJob).where(PreviewJob.id == first).with_for_update())
        result = claimed(sessions, project)
        assert result is not None and result.id == second


def test_heartbeat_and_all_stale_token_transitions_are_fenced(queue_pg):
    sessions, project, add_job, _settings, _client = queue_pg
    add_job()
    owner = claimed(sessions, project)
    stale = replace(owner, claim_token=uuid4())
    with sessions() as session, session.begin():
        repository = PreviewJobRepository(session)
        assert repository.heartbeat(owner, 30)
        assert repository.set_stage(owner, "encoding", 30)
        assert not repository.heartbeat(stale, 30)
        assert not repository.set_stage(stale, "publishing", 30)
        assert not repository.fail_owned(stale, "preview_timeout")
        assert repository.lock_owned_for_publish(stale) is None


def test_expired_claim_retries_once_with_new_token_then_fails(queue_pg):
    sessions, project, add_job, _settings, _client = queue_pg
    job_id = add_job()
    first = claimed(sessions, project)
    expire(sessions, job_id)
    with sessions() as session, session.begin():
        repository = PreviewJobRepository(session)
        assert not repository.heartbeat(first, 30)
        assert not repository.fail_owned(first, "preview_timeout")
        assert repository.recover_expired(project_id=project) == 1
    second = claimed(sessions, project)
    assert second.id == first.id and second.attempts == 2
    assert second.claim_token != first.claim_token
    with sessions() as session, session.begin():
        assert not PreviewJobRepository(session).set_stage(first, "publishing", 30)
    expire(sessions, job_id)
    with sessions() as session, session.begin():
        repository = PreviewJobRepository(session)
        assert repository.recover_expired(project_id=project) == 1
    assert claimed(sessions, project) is None
    with sessions() as session:
        job = session.get(PreviewJob, job_id)
        assert (job.status, job.attempts, job.error_code) == (
            "failed",
            2,
            "preview_attempts_exhausted",
        )


def test_deleted_project_jobs_cannot_claim_or_renew(queue_pg):
    sessions, project, add_job, _settings, client = queue_pg
    running_id, queued_id = add_job(), add_job()
    owner = claimed(sessions, project)
    assert owner.id == running_id
    assert client.delete(f"/api/v1/projects/{project}").status_code == 204
    assert claimed(sessions, project) is None
    with sessions() as session, session.begin():
        repository = PreviewJobRepository(session)
        assert not repository.heartbeat(owner, 30)
        assert repository.recover_expired(project_id=project) == 2
    with sessions() as session:
        assert session.get(PreviewJob, queued_id).error_code == "preview_project_unavailable"


def test_wall_clock_rejects_lease_after_wait_in_same_transaction(queue_pg):
    sessions, project, add_job, _settings, _client = queue_pg
    add_job()
    owner = claimed(sessions, project, lease=0.2)
    with sessions() as session, session.begin():
        session.scalar(select(database_now()))
        sleep(0.3)
        repository = PreviewJobRepository(session)
        assert not repository.heartbeat(owner, 30)
        assert repository.lock_owned_for_publish(owner) is None


def test_worker_commits_before_process_and_timeout_is_persisted(queue_pg):
    sessions, project, add_job, settings, _client = queue_pg
    job_id = add_job()
    configured = settings.model_copy(update={"preview_ffmpeg_timeout_seconds": 0.25})

    def processor(owner, control):
        with sessions() as verification:
            job = verification.get(PreviewJob, owner.id)
            assert job.status == "running" and job.claim_token == owner.claim_token
        control.set_stage("encoding")
        ProcessRunner(configured, control.stopped, control.heartbeat).run(
            [sys.executable, "-c", "import time; time.sleep(30)"]
        )

    assert PreviewWorker(configured, sessions, Event(), processor, project_id=project).run_one()
    with sessions() as session:
        job = session.get(PreviewJob, job_id)
        assert job.status == "failed" and job.error_code == "preview_timeout"


def test_equivalent_failed_job_allows_new_explicit_request(queue_pg):
    sessions, project, add_job, _settings, _client = queue_pg
    job_id = add_job()
    owner = claimed(sessions, project)
    with sessions() as session, session.begin():
        repository = PreviewJobRepository(session)
        job = repository.get_for_project(project, job_id)
        key = job.idempotency_key
        assert repository.get_equivalent(project, key).id == job_id
        assert repository.fail_owned(owner, "preview_encoding_failed")
    with sessions() as session, session.begin():
        repository = PreviewJobRepository(session)
        assert repository.get_equivalent(project, key) is None
        previous = repository.get_for_project(project, job_id)
        retry = PreviewJob(
            project_id=project,
            edit_spec_revision_id=previous.edit_spec_revision_id,
            preview_start_ms=0,
            duration_ms=1000,
            idempotency_key=key,
        )
        repository.add(retry)
        session.flush()
        assert retry.id != job_id
