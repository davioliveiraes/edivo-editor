"""Opt-in PostgreSQL/API evidence scoped to new synthetic R05 projects.

Run only after migration/up coordinated by the main agent, while the production
worker does not claim renders. EDIVO_QA_DATABASE_URL is copied privately from
the API's environment; conftest intentionally replaces EDIVO_DATABASE_URL.
Each fixture terminalizes only its own active jobs and soft-deletes its project.
No originals, completed artifacts, revisions or volumes are removed.
"""

import hashlib
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier, Event
from time import sleep
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import create_engine, inspect, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models.edit_spec_revision import EditSpecRevision
from app.models.render_job import RenderJob
from app.repositories.preview_jobs import database_now
from app.repositories.projects import ProjectRepository
from app.repositories.render_jobs import RenderJobRepository
from app.schemas.renders import RenderCreate
from app.services.renders import RenderService

pytestmark = pytest.mark.skipif(
    os.environ.get("EDIVO_QA_RENDER_POSTGRES") != "1",
    reason="requires explicit real PostgreSQL/API R05 operational QA",
)


@pytest.fixture
def render_pg(tmp_path):
    actual_url = os.environ.get("EDIVO_QA_DATABASE_URL")
    if not actual_url:
        pytest.fail("real_render_database_url_required")
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
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=15) as client:
        try:
            response = client.post(
                "/api/v1/projects", json={"name": f"QA R05 relational {uuid4()}"}
            )
            assert response.status_code == 201
            project_id = UUID(response.json()["id"])
            response = client.post(
                f"/api/v1/projects/{project_id}/media",
                files={"file": ("synthetic.mp4", source.read_bytes(), "video/mp4")},
            )
            assert response.status_code == 201
            response = client.put(
                f"/api/v1/projects/{project_id}/edit-spec",
                json={
                    "expected_revision": 0,
                    "schema_version": 1,
                    "source_media_id": response.json()["id"],
                    "trim": {"start_ms": 0, "end_ms": 1000},
                    "canvas": {"preset": "9:16", "fit": "cover", "focal_x": 0.5, "focal_y": 0.5},
                },
            )
            assert response.status_code == 201
            revision_id = UUID(response.json()["id"])
            base = f"/api/v1/projects/{project_id}/renders"
            payload = {"edit_spec_revision_id": str(revision_id), "profile": "standard"}
            yield sessions, project_id, revision_id, base, payload, client
        finally:
            if project_id is not None:
                with sessions() as session, session.begin():
                    session.execute(
                        update(RenderJob)
                        .where(
                            RenderJob.project_id == project_id,
                            RenderJob.status.in_(("queued", "running")),
                        )
                        .values(
                            status="failed",
                            stage="failed",
                            claim_token=None,
                            lease_expires_at=None,
                            finished_at=database_now(),
                            updated_at=database_now(),
                            error_code="render_qa_finished",
                        )
                    )
                assert client.delete(f"/api/v1/projects/{project_id}").status_code in (204, 404)
            engine.dispose()


def queued(context, *, key=None):
    _sessions, _pid, _rid, base, payload, client = context
    response = client.post(base, json=payload | ({"idempotency_key": key} if key else {}))
    assert response.status_code == 202, response.text
    return UUID(response.json()["id"])


def claim(context, *, lease=30):
    sessions, pid, *_ = context
    with sessions() as session, session.begin():
        return RenderJobRepository(session).claim_next(lease, project_id=pid)


def expire(context, job_id):
    with context[0]() as session, session.begin():
        session.execute(
            update(RenderJob)
            .where(RenderJob.id == job_id)
            .values(lease_expires_at=database_now() - timedelta(seconds=1))
        )


def test_real_schema_revision_constraints_indexes_and_trigger(render_pg):
    sessions, *_ = render_pg
    with sessions() as session:
        assert (
            session.scalar(text("SELECT version_num FROM alembic_version"))
            == "0005_r05_render_exports"
        )
        inspector = inspect(session.bind)
        indexes = {item["name"]: item for item in inspector.get_indexes("render_jobs")}
        assert indexes["uq_render_jobs_active_project"]["unique"]
        assert {
            "ix_render_jobs_history",
            "ix_render_jobs_queue",
            "ix_render_jobs_lease",
        } <= indexes.keys()
        checks = {item["name"] for item in inspector.get_check_constraints("render_jobs")}
        assert {
            "ck_render_jobs_state",
            "ck_render_jobs_completed_artifact",
            "ck_render_jobs_attempts",
        } <= checks
        assert (
            session.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger "
                    "WHERE tgname='edit_spec_revisions_immutable' AND NOT tgisinternal"
                )
            )
            == 1
        )


def test_real_concurrent_same_key_creates_one_job(render_pg):
    sessions, pid, _rid, base, payload, _client = render_pg
    barrier, key = Barrier(2), str(uuid4())

    def request():
        with httpx.Client(base_url="http://127.0.0.1:8000", timeout=15) as client:
            barrier.wait(timeout=5)
            response = client.post(base, json=payload | {"idempotency_key": key})
            return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(request) for _ in range(2)]
        results = [future.result(timeout=20) for future in futures]
    assert [result[0] for result in results] == [202, 202]
    assert results[0][1]["id"] == results[1][1]["id"]
    with sessions() as session:
        assert RenderJobRepository(session).count_active(pid) == 1


def test_real_concurrent_distinct_keys_respect_project_limit(render_pg):
    _sessions, _pid, _rid, base, payload, _client = render_pg
    barrier = Barrier(2)

    def request():
        with httpx.Client(base_url="http://127.0.0.1:8000", timeout=15) as client:
            barrier.wait(timeout=5)
            return client.post(base, json=payload | {"idempotency_key": str(uuid4())})

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(request) for _ in range(2)]
        results = [future.result(timeout=20) for future in futures]
    assert sorted(response.status_code for response in results) == [202, 409]
    assert (
        next(response for response in results if response.status_code == 409).json()["code"]
        == "render_active_limit"
    )


def test_real_concurrent_projects_compete_for_last_global_admission_slot(
    render_pg, settings, tmp_path, monkeypatch
):
    """Actual PostgreSQL advisory lock; SQLite units do not prove this race."""
    from app.core.errors import AppError

    sessions, first_pid, first_rid, _base, _payload, client = render_pg
    configured = settings.model_copy(update={"render_max_active_total": 1})
    # The API and this test share the real storage, not the isolated unit root.
    from app.core.config import Settings

    configured.storage_root = Settings().storage_root
    second_pid = None
    try:
        response = client.post(
            "/api/v1/projects", json={"name": f"QA R05 global admission {uuid4()}"}
        )
        assert response.status_code == 201
        second_pid = UUID(response.json()["id"])
        response = client.post(
            f"/api/v1/projects/{second_pid}/media",
            files={
                "file": ("synthetic.mp4", (tmp_path / "synthetic.mp4").read_bytes(), "video/mp4")
            },
        )
        assert response.status_code == 201
        response = client.put(
            f"/api/v1/projects/{second_pid}/edit-spec",
            json={
                "expected_revision": 0,
                "schema_version": 1,
                "source_media_id": response.json()["id"],
                "trim": {"start_ms": 0, "end_ms": 1000},
                "canvas": {"preset": "9:16", "fit": "cover", "focal_x": 0.5, "focal_y": 0.5},
            },
        )
        assert response.status_code == 201
        second_rid = UUID(response.json()["id"])
        with sessions() as session:
            assert RenderJobRepository(session).count_active_total() == 0
        barrier = Barrier(2)
        real_lock = RenderJobRepository.lock_global_admission

        def contend_after_independent_project_locks(repository):
            barrier.wait(timeout=5)
            return real_lock(repository)

        monkeypatch.setattr(
            RenderJobRepository, "lock_global_admission", contend_after_independent_project_locks
        )

        def submit(project_id, revision_id):
            with sessions() as session:
                try:
                    result, reused = RenderService(session, configured).create(
                        project_id,
                        RenderCreate(
                            edit_spec_revision_id=revision_id,
                            profile="standard",
                            idempotency_key=uuid4(),
                        ),
                    )
                    assert not reused
                    return 202, result.id
                except AppError as error:
                    session.rollback()
                    return error.status_code, error.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(submit, first_pid, first_rid),
                executor.submit(submit, second_pid, second_rid),
            ]
            results = [future.result(timeout=20) for future in futures]
        assert sorted(result[0] for result in results) == [202, 409]
        assert next(result[1] for result in results if result[0] == 409) == "render_queue_full"
        with sessions() as session:
            assert RenderJobRepository(session).count_active_total() == 1
    finally:
        if second_pid is not None:
            with sessions() as session, session.begin():
                session.execute(
                    update(RenderJob)
                    .where(
                        RenderJob.project_id == second_pid,
                        RenderJob.status.in_(("queued", "running")),
                    )
                    .values(
                        status="failed",
                        stage="failed",
                        claim_token=None,
                        lease_expires_at=None,
                        finished_at=database_now(),
                        updated_at=database_now(),
                        error_code="render_qa_finished",
                    )
                )
            assert client.delete(f"/api/v1/projects/{second_pid}").status_code in (204, 404)


def test_real_key_remains_reserved_after_cancel(render_pg):
    _sessions, _pid, _rid, base, payload, client = render_pg
    key = str(uuid4())
    identifier = queued(render_pg, key=key)
    canceled = client.post(f"{base}/{identifier}/cancel")
    assert canceled.status_code == 200 and canceled.json()["status"] == "canceled"
    replay = client.post(base, json=payload | {"idempotency_key": key})
    assert replay.status_code == 200 and replay.json()["id"] == str(identifier)
    conflict = client.post(base, json=payload | {"idempotency_key": key, "profile": "high"})
    assert conflict.status_code == 409 and conflict.json()["code"] == "idempotency_conflict"
    assert queued(render_pg, key=str(uuid4())) != identifier


def test_real_atomic_claim_two_candidates_only_one_wins(render_pg):
    identifier = queued(render_pg)
    barrier = Barrier(2)

    def candidate():
        barrier.wait(timeout=5)
        return claim(render_pg)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(candidate) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    winners = [item for item in results if item is not None]
    assert len(winners) == 1 and winners[0].id == identifier and winners[0].attempts == 1


def test_real_progress_heartbeat_fencing_and_running_cancel(render_pg):
    sessions, _pid, _rid, base, _payload, client = render_pg
    identifier = queued(render_pg)
    owner = claim(render_pg)
    stale = replace(owner, claim_token=uuid4())
    with sessions() as session, session.begin():
        repository = RenderJobRepository(session)
        assert repository.heartbeat(owner, 30)
        assert repository.set_stage(owner, "encoding", 30)
        assert repository.update_progress(owner, 700)
        assert repository.update_progress(owner, 600)
        assert not repository.update_progress(stale, 900)
        assert not repository.heartbeat(stale, 30)
        assert not repository.fail_owned(stale, "render_timeout")
    response = client.post(f"{base}/{identifier}/cancel")
    assert response.status_code == 202 and response.json()["status"] == "running"
    assert response.json()["progress_encoded_ms"] == 700
    with sessions() as session, session.begin():
        repository = RenderJobRepository(session)
        assert repository.control_state(owner) == "cancel_requested"
        assert not repository.heartbeat(owner, 30)
        assert not repository.update_progress(owner, 900)
        assert repository.lock_owned_for_publish(owner) is None
        # No subprocess was started by this relational test.
        assert repository.cancel_owned(owner)
    assert client.get(f"{base}/{identifier}").json()["status"] == "canceled"


def test_real_recovery_retries_once_then_fails_with_new_token(render_pg):
    sessions, pid, *_ = render_pg
    identifier = queued(render_pg)
    first = claim(render_pg)
    expire(render_pg, identifier)
    with sessions() as session, session.begin():
        assert RenderJobRepository(session).recover_expired(project_id=pid) == 1
    second = claim(render_pg)
    assert (
        second.id == first.id and second.attempts == 2 and second.claim_token != first.claim_token
    )
    with sessions() as session, session.begin():
        assert not RenderJobRepository(session).update_progress(first, 800)
    expire(render_pg, identifier)
    with sessions() as session, session.begin():
        assert RenderJobRepository(session).recover_expired(project_id=pid) == 1
    assert claim(render_pg) is None
    with sessions() as session:
        assert session.get(RenderJob, identifier).error_code == "render_attempts_exhausted"


def test_real_lock_wait_cannot_extend_expired_owner(render_pg):
    sessions, *_ = render_pg
    identifier = queued(render_pg)
    owner = claim(render_pg, lease=0.3)
    entered = Event()

    def progress():
        with sessions() as session, session.begin():
            entered.set()
            return RenderJobRepository(session).update_progress(owner, 100)

    with ThreadPoolExecutor(max_workers=1) as executor:
        with sessions() as locked, locked.begin():
            locked.scalar(select(RenderJob).where(RenderJob.id == identifier).with_for_update())
            future = executor.submit(progress)
            assert entered.wait(5)
            sleep(0.4)
            assert not future.done()
        assert future.result(timeout=5) is False
    with sessions() as session:
        assert session.get(RenderJob, identifier).progress_encoded_ms is None


def test_real_cancel_committed_before_publish_prevents_ownership(render_pg):
    sessions, pid, _rid, base, _payload, client = render_pg
    identifier = queued(render_pg)
    owner = claim(render_pg)
    assert client.post(f"{base}/{identifier}/cancel").status_code == 202
    with sessions() as session, session.begin():
        assert ProjectRepository(session).get_active(pid, for_update=True) is not None
        repository = RenderJobRepository(session)
        assert repository.lock_owned_for_publish(owner) is None
        assert repository.cancel_owned(owner)


def test_real_terminal_commit_wins_over_waiting_cancel(render_pg):
    sessions, pid, _rid, base, _payload, _client = render_pg
    identifier = queued(render_pg)
    owner = claim(render_pg)
    entered = Event()

    def cancel():
        with httpx.Client(base_url="http://127.0.0.1:8000", timeout=15) as client:
            entered.set()
            return client.post(f"{base}/{identifier}/cancel")

    with ThreadPoolExecutor(max_workers=1) as executor:
        with sessions() as locked, locked.begin():
            ProjectRepository(locked).get_active(pid, for_update=True)
            repository = RenderJobRepository(locked)
            assert repository.lock_owned_for_publish(owner) is not None
            assert repository.fail_owned(owner, "render_validation_failed")
            future = executor.submit(cancel)
            assert entered.wait(5)
            sleep(0.2)
            assert not future.done()
        result = future.result(timeout=15)
    assert result.status_code == 409 and result.json()["code"] == "render_not_cancelable"


def test_real_foreign_keys_state_and_active_unique_are_enforced(render_pg):
    sessions, pid, rid, *_ = render_pg

    def values(**changes):
        return (
            dict(
                project_id=pid,
                edit_spec_revision_id=rid,
                profile="standard",
                render_engine_version="r05-1",
                request_fingerprint="a" * 64,
            )
            | changes
        )

    for changes, sqlstate in [
        ({"edit_spec_revision_id": uuid4()}, "23503"),
        ({"status": "running", "stage": "encoding"}, "23514"),
    ]:
        with sessions() as session:
            session.add(RenderJob(**values(**changes)))
            with pytest.raises(IntegrityError) as error:
                session.commit()
            assert error.value.orig.sqlstate == sqlstate
            session.rollback()
    queued(render_pg)
    with sessions() as session:
        session.add(RenderJob(**values(idempotency_key=str(uuid4()))))
        with pytest.raises(IntegrityError) as error:
            session.commit()
        assert error.value.orig.sqlstate == "23505"
        session.rollback()


def test_real_revision_trigger_still_blocks_update_and_delete(render_pg):
    sessions, _pid, rid, *_ = render_pg
    with sessions() as session:
        before = hashlib.sha256(
            json.dumps(session.get(EditSpecRevision, rid).spec_json, sort_keys=True).encode()
        ).hexdigest()
    for statement in [
        text("UPDATE edit_spec_revisions SET schema_version=2 WHERE id=:id"),
        text("DELETE FROM edit_spec_revisions WHERE id=:id"),
    ]:
        with sessions() as session:
            with pytest.raises(Exception) as error:
                session.execute(statement, {"id": rid})
            assert error.value.orig.sqlstate == "55000"
            session.rollback()
    with sessions() as session:
        after = hashlib.sha256(
            json.dumps(session.get(EditSpecRevision, rid).spec_json, sort_keys=True).encode()
        ).hexdigest()
    assert before == after
