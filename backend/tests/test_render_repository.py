"""Fencing/state units; compiled SQL checks are not PostgreSQL concurrency proof."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import DateTime, literal, select
from sqlalchemy.dialects import postgresql
from test_render_job_model import job_context, make_job  # noqa: F401

from app.models.project import Project, utc_now
from app.models.render_job import RenderJob
from app.repositories import render_jobs as module
from app.repositories.render_jobs import (
    RENDER_ADMISSION_LOCK_KEY,
    RenderClaim,
    RenderJobRepository,
)


def test_global_admission_serializes_postgresql_count_insert_with_transaction_lock():
    statements = []

    class Session:
        def get_bind(self):
            return SimpleNamespace(dialect=postgresql.dialect())

        def execute(self, statement):
            statements.append(statement.compile(dialect=postgresql.dialect()))

        def scalar(self, statement):
            statements.append(statement.compile(dialect=postgresql.dialect()))
            return 7

    repository = RenderJobRepository(Session())
    repository.lock_global_admission()
    assert repository.count_active_total() == 7
    assert len(statements) == 2
    assert "pg_advisory_xact_lock" in str(statements[0])
    assert list(statements[0].params.values()) == [RENDER_ADMISSION_LOCK_KEY]
    assert 2**31 < RENDER_ADMISSION_LOCK_KEY < 2**63
    count_sql = str(statements[1])
    assert "count(*)" in count_sql and "render_jobs.status IN" in count_sql
    assert "JOIN" not in count_sql and "projects" not in count_sql
    assert tuple(next(iter(statements[1].params.values()))) == ("queued", "running")


def test_isolated_sqlite_units_do_not_execute_or_claim_postgresql_admission_lock(
    domain_sessions, monkeypatch
):
    with domain_sessions() as session:
        monkeypatch.setattr(
            session, "execute", lambda *_: pytest.fail("no PostgreSQL lock in SQLite")
        )
        RenderJobRepository(session).lock_global_admission()


@pytest.fixture
def repository_context(domain_sessions, job_context, monkeypatch):  # noqa: F811
    now = utc_now()
    monkeypatch.setattr(module, "database_now", lambda: literal(now, type_=DateTime(timezone=True)))
    with domain_sessions() as session, session.begin():
        job = make_job(
            job_context,
            status="running",
            stage="encoding",
            attempts=1,
            claim_token=uuid4(),
            heartbeat_at=now,
            lease_expires_at=now + timedelta(seconds=30),
        )
        session.add(job)
        session.flush()
        claim = RenderClaim(
            job.id,
            job.project_id,
            job.edit_spec_revision_id,
            job.claim_token,
            1,
            2,
            job.profile,
            job.render_engine_version,
        )
    return domain_sessions, claim, now


def test_claim_query_skips_locked_and_only_locks_jobs():
    sql = str(RenderJobRepository.claim_query().compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE OF render_jobs SKIP LOCKED" in sql
    assert "projects.deleted_at IS NULL" in sql
    assert "render_jobs.cancel_requested_at IS NULL" in sql
    assert "render_jobs.attempts < render_jobs.max_attempts" in sql
    assert "ORDER BY render_jobs.created_at, render_jobs.id" in sql


def test_ownership_contains_wall_clock_token_attempt_and_cancellation():
    claim = RenderClaim(uuid4(), uuid4(), uuid4(), uuid4(), 1, 2, "standard", "r05-1")
    query = select(RenderJob.id).where(*RenderJobRepository.ownership(claim))
    sql = str(query.compile(dialect=postgresql.dialect()))
    assert "clock_timestamp()" in sql and "claim_token" in sql and "attempts =" in sql
    assert "lease_expires_at >" in sql and "cancel_requested_at IS NULL" in sql
    assert "projects.deleted_at IS NULL" in sql


def test_progress_is_monotonic_per_attempt_and_cannot_update_after_cancel(repository_context):
    sessions, claim, now = repository_context
    with sessions() as session, session.begin():
        repository = RenderJobRepository(session)
        assert repository.update_progress(claim, 7000)
        assert repository.update_progress(claim, 6000)
        assert (
            session.scalar(select(RenderJob.progress_encoded_ms).where(RenderJob.id == claim.id))
            == 7000
        )
        job = session.get(RenderJob, claim.id)
        job.cancel_requested_at = now
        session.flush()
        assert not repository.update_progress(claim, 8000)
        assert repository.control_state(claim) == "cancel_requested"
        assert not repository.is_owned(claim)
        assert repository.is_owned(claim, allow_cancel=True)


@pytest.mark.parametrize("value", [-1, 1.5, "3", True, 2**63])
def test_invalid_progress_rejected(repository_context, value):
    sessions, claim, _ = repository_context
    with sessions() as session, pytest.raises(ValueError, match="invalid_render_progress"):
        RenderJobRepository(session).update_progress(claim, value)


def test_stale_token_is_fenced_for_all_transitions(repository_context):
    sessions, owner, now = repository_context
    stale = replace(owner, claim_token=uuid4())
    with sessions() as session, session.begin():
        repository = RenderJobRepository(session)
        assert repository.control_state(owner) == "owned"
        assert repository.control_state(stale) == "lost"
        assert not repository.update_progress(stale, 1000)
        assert not repository.fail_owned(stale, "render_timeout")
        assert not repository.cancel_owned(stale)
        assert repository.lock_owned_for_publish(stale) is None
        job = session.get(RenderJob, owner.id)
        job.lease_expires_at = now - timedelta(seconds=1)
        session.flush()
        assert repository.control_state(owner) == "lost"
        assert not repository.fail_owned(owner, "render_timeout")


def test_cancel_owned_requires_intent_and_then_prevents_reopening(repository_context):
    sessions, claim, now = repository_context
    with sessions() as session, session.begin():
        repository = RenderJobRepository(session)
        assert not repository.cancel_owned(claim)
        job = session.get(RenderJob, claim.id)
        job.cancel_requested_at = now
        session.flush()
        assert repository.cancel_owned(claim)
        assert not repository.fail_owned(claim, "render_timeout", retry=True)
        assert not repository.update_progress(claim, 9000)
    with sessions() as session:
        job = session.get(RenderJob, claim.id)
        assert job.status == job.stage == "canceled" and job.finished_at is not None
        assert job.claim_token is job.lease_expires_at is None


@pytest.mark.parametrize(
    "retry,attempt,expected", [(False, 1, "failed"), (True, 1, "queued"), (True, 2, "failed")]
)
def test_failure_is_finite_and_cancellation_wins(repository_context, retry, attempt, expected):
    sessions, owner, now = repository_context
    claim = replace(owner, attempts=attempt)
    with sessions() as session, session.begin():
        job = session.get(RenderJob, claim.id)
        job.attempts = attempt
        session.flush()
        assert RenderJobRepository(session).fail_owned(claim, "render_timeout", retry=retry)
    with sessions() as session:
        job = session.get(RenderJob, claim.id)
        assert job.status == expected and job.claim_token is None
        assert (job.finished_at is None) == (expected == "queued")


def test_failure_after_cancel_request_cannot_requeue(repository_context):
    sessions, claim, now = repository_context
    with sessions() as session, session.begin():
        job = session.get(RenderJob, claim.id)
        job.cancel_requested_at = now
        session.flush()
        assert RenderJobRepository(session).fail_owned(
            claim, "render_worker_interrupted", retry=True
        )
    with sessions() as session:
        job = session.get(RenderJob, claim.id)
        assert job.status == "canceled" and job.error_code is None


@pytest.mark.parametrize(
    "attempt,cancel,deleted,expected",
    [
        (1, False, False, "queued"),
        (2, False, False, "failed"),
        (1, True, False, "canceled"),
        (1, False, True, "failed"),
    ],
)
def test_expired_claim_recovery_preserves_cancel_and_attempt_limit(
    repository_context, attempt, cancel, deleted, expected
):
    sessions, claim, now = repository_context
    with sessions() as session, session.begin():
        job = session.get(RenderJob, claim.id)
        job.attempts = attempt
        job.lease_expires_at = now - timedelta(seconds=1)
        if cancel:
            job.cancel_requested_at = now
        if deleted:
            session.get(Project, claim.project_id).deleted_at = now
    with sessions() as session, session.begin():
        assert RenderJobRepository(session).recover_expired(project_id=claim.project_id) == 1
    with sessions() as session:
        job = session.get(RenderJob, claim.id)
        assert job.status == expected and job.claim_token is None
        assert job.attempts == attempt


def test_invalid_error_code_cannot_store_external_details(repository_context):
    sessions, claim, _ = repository_context
    with sessions() as session:
        for code in ["raw /private/password", "preview_timeout", "render_" + "x" * 58]:
            with pytest.raises(ValueError, match="invalid_render_error_code"):
                RenderJobRepository(session).fail_owned(claim, code)


def test_heartbeat_and_stage_queries_are_fenced_and_use_wall_clock():
    statements = []

    class Session:
        def scalar(self, statement):
            statements.append(str(statement.compile(dialect=postgresql.dialect())))
            return True

        def execute(self, statement):
            statements.append(str(statement.compile(dialect=postgresql.dialect())))
            return SimpleNamespace(rowcount=1)

    claim = RenderClaim(uuid4(), uuid4(), uuid4(), uuid4(), 1, 2, "standard", "r05-1")
    repository = RenderJobRepository(Session())
    assert repository.heartbeat(claim, 30)
    assert repository.set_stage(claim, "validating", 30)
    assert all(
        "clock_timestamp()" in sql and "cancel_requested_at IS NULL" in sql for sql in statements
    )
    with pytest.raises(ValueError, match="invalid_render_stage"):
        repository.set_stage(claim, "completed", 30)


def test_lock_wait_requires_fresh_ownership_before_mutation():
    answers = iter([object(), False])

    class Session:
        def scalar(self, statement):
            return next(answers)

        def execute(self, statement):
            pytest.fail("expired claim must not mutate after waiting for lock")

    claim = RenderClaim(uuid4(), uuid4(), uuid4(), uuid4(), 1, 2, "standard", "r05-1")
    assert not RenderJobRepository(Session()).update_progress(claim, 1000)
