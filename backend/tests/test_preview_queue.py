"""SQL/worker orchestration units; real locking is covered by the PG opt-in suite."""

from contextlib import contextmanager
from threading import Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError
from test_edit_spec import plan, seed_source

from app.models.preview_job import PreviewJob
from app.repositories import preview_jobs as repository_module
from app.repositories.preview_jobs import PreviewClaim, PreviewJobRepository
from app.worker import preview_worker
from app.worker.preview_worker import PreviewWorker
from app.worker.supervisor import LeaseLost, PreviewProcessError, ProcessStopped


def claim():
    return PreviewClaim(uuid4(), uuid4(), uuid4(), uuid4(), 1, 0, 1000, 1)


def test_claim_sql_locks_only_jobs_and_skips_locked():
    project = uuid4()
    query = PreviewJobRepository.claim_query(project)
    sql = str(query.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE OF preview_jobs SKIP LOCKED" in sql
    assert "projects.deleted_at IS NULL" in sql
    assert "preview_jobs.project_id =" in sql
    assert "ORDER BY preview_jobs.created_at, preview_jobs.id" in sql


def test_fencing_predicates_include_live_wall_clock_and_active_project():
    sql = " AND ".join(
        str(predicate.compile(dialect=postgresql.dialect()))
        for predicate in PreviewJobRepository.ownership(claim())
    )
    assert "clock_timestamp()" in sql
    assert "claim_token" in sql
    assert "status" in sql
    assert "projects.deleted_at IS NULL" in sql
    assert "project_id" in sql


@pytest.mark.parametrize("attempts", [0, 3, True, 1.0])
def test_attempt_limit_is_hard_capped(attempts):
    with pytest.raises(ValueError, match="invalid_preview_attempt_limit"):
        PreviewJobRepository(None, max_attempts=attempts)


@pytest.fixture
def orchestration(settings, monkeypatch):
    events = []
    current = claim()

    class Session:
        def __enter__(self):
            events.append("session_open")
            return self

        def __exit__(self, *_args):
            events.append("session_close")

        @contextmanager
        def begin(self):
            events.append("transaction_begin")
            yield
            events.append("transaction_commit")

    class Repository:
        def __init__(self, _session, **_kwargs):
            pass

        def recover_expired(self, **kwargs):
            events.append(("recovery", kwargs))

        def claim_next(self, *_args, **_kwargs):
            return current

        def fail_owned(self, received, code, *, retry=False):
            events.append(("failure", received.id, code, retry))
            return True

        def heartbeat(self, *_args):
            events.append("heartbeat")
            return True

        def set_stage(self, *_args):
            events.append("stage")
            return True

    monkeypatch.setattr(preview_worker, "PreviewJobRepository", Repository)
    return settings, Session, events, current


def test_claim_transaction_closes_before_processor(orchestration):
    settings, sessions, events, current = orchestration

    def processor(received, control):
        assert received == current
        assert events[-2:] == ["transaction_commit", "session_close"]
        assert control.heartbeat()
        control.set_stage("encoding")

    worker = PreviewWorker(settings, sessions, Event(), processor, project_id=current.project_id)
    assert worker.run_one()
    assert ("failure", current.id, "preview_processing_incomplete", False) in events


@pytest.mark.parametrize(
    "error,expected_code,retry",
    [
        (ProcessStopped(), "preview_worker_interrupted", True),
        (PreviewProcessError("preview_timeout"), "preview_timeout", False),
        (RuntimeError("/private/secret"), "preview_processing_failed", False),
    ],
)
def test_worker_failures_are_sanitized(orchestration, error, expected_code, retry, capsys):
    settings, sessions, events, current = orchestration

    def processor(*_args):
        raise error

    assert PreviewWorker(settings, sessions, Event(), processor).run_one()
    assert ("failure", current.id, expected_code, retry) in events
    assert "/private/secret" not in capsys.readouterr().err


def test_lost_lease_does_not_mutate_job_owned_by_successor(orchestration):
    settings, sessions, events, _current = orchestration

    def processor(*_args):
        raise LeaseLost()

    assert PreviewWorker(settings, sessions, Event(), processor).run_one()
    assert not any(isinstance(item, tuple) and item[0] == "failure" for item in events)


def test_stop_prevents_claim_and_idle_wait_is_bounded(orchestration, monkeypatch):
    settings, sessions, events, _current = orchestration
    stopped = Event()
    stopped.set()
    worker = PreviewWorker(settings, sessions, stopped, lambda *_args: None)
    assert not worker.run_one()
    assert not events
    calls = []

    class StopAfterWait:
        def is_set(self):
            return bool(calls)

        def wait(self, seconds):
            calls.append(seconds)

    worker.stopped = StopAfterWait()
    monkeypatch.setattr(worker, "run_one", lambda: False)
    worker.run()
    assert calls == [settings.preview_worker_poll_seconds]


def test_heartbeat_database_failure_reports_lease_lost(settings, capsys):
    def broken_session():
        raise SQLAlchemyError("postgresql password /private")

    control = preview_worker.LeaseController(settings, broken_session, claim(), Event())
    assert not control.heartbeat()
    with pytest.raises(LeaseLost):
        control.set_stage("validating")
    assert "password" not in capsys.readouterr().err


def test_worker_requires_explicit_processor(settings):
    with pytest.raises(TypeError):
        PreviewWorker(settings, lambda: None, Event())


def test_claim_stage_and_failure_events_follow_commit(orchestration, monkeypatch):
    settings, sessions, events, current = orchestration
    records = []

    def logged(message, *, extra):
        assert events[-2:] == ["transaction_commit", "session_close"]
        records.append((message, extra))

    monkeypatch.setattr(preview_worker.logger, "info", logged)
    monkeypatch.setattr(preview_worker.logger, "warning", logged)

    def processor(_claim, control):
        control.set_stage("encoding")
        raise PreviewProcessError("preview_timeout")

    assert PreviewWorker(settings, sessions, Event(), processor).run_one()
    assert [record[0] for record in records] == [
        "preview_job_claimed",
        "preview_job_stage_changed",
        "preview_job_failed",
    ]
    assert all(record[1]["job_id"] == str(current.id) for record in records)
    assert records[-1][1]["error_code"] == "preview_timeout"


def test_failed_stage_update_never_emits_success_event(orchestration, monkeypatch):
    settings, sessions, _events, current = orchestration
    records = []
    monkeypatch.setattr(preview_worker.logger, "info", lambda *args, **kwargs: records.append(args))
    monkeypatch.setattr(preview_worker.PreviewJobRepository, "set_stage", lambda *_args: False)
    control = preview_worker.LeaseController(settings, sessions, current, Event())
    with pytest.raises(LeaseLost):
        control.set_stage("encoding")
    assert records == []


def test_lowered_attempt_limit_terminalizes_previously_requeued_job(
    domain_client, domain_sessions, monkeypatch
):
    """Relational unit only; SQLite does not prove PostgreSQL row-lock semantics."""
    project_id = domain_client.post("/api/v1/projects", json={"name": "Reduced attempts"}).json()[
        "id"
    ]
    source_id = seed_source(domain_sessions, project_id)
    revision = domain_client.put(
        f"/api/v1/projects/{project_id}/edit-spec", json=plan(source_id)
    ).json()
    monkeypatch.setattr(repository_module, "database_now", lambda: func.current_timestamp())
    project = UUID(project_id)
    with domain_sessions() as session, session.begin():
        job = PreviewJob(
            project_id=project,
            edit_spec_revision_id=UUID(revision["id"]),
            preview_start_ms=0,
            duration_ms=1000,
            idempotency_key="b" * 64,
            status="queued",
            stage="queued",
            attempts=1,
        )
        session.add(job)
        session.flush()
        job_id = job.id
    with domain_sessions() as session, session.begin():
        repository = PreviewJobRepository(session, max_attempts=1)
        assert repository.count_active(project) == 1
        assert repository.claim_next(30, project_id=project) is None
        assert repository.recover_expired(project_id=project) == 1
    with domain_sessions() as session:
        job = session.get(PreviewJob, job_id)
        assert job.status == job.stage == "failed"
        assert job.error_code == "preview_attempts_exhausted"
        assert job.attempts == 1 and job.finished_at is not None
        assert PreviewJobRepository(session).count_active(project) == 0
