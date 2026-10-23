"""Shared FIFO selection units; PostgreSQL lock behavior has separate opt-in tests."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.models.preview_job import PreviewJob
from app.repositories.preview_jobs import PreviewClaim
from app.repositories.render_jobs import RenderClaim
from app.worker import scheduler as module
from app.worker.scheduler import WorkerScheduler


@pytest.fixture
def selection(settings, monkeypatch):
    events = []
    now = datetime.now(UTC)
    candidates = SimpleNamespace(preview=None, render=None)

    class Session:
        def __enter__(self):
            events.append("session_open")
            return self

        def __exit__(self, *_args):
            events.append("session_close")

        @contextmanager
        def begin(self):
            yield
            events.append("transaction_commit")

        def scalar(self, query):
            kind = "preview" if query.column_descriptions[0]["entity"] is PreviewJob else "render"
            events.append(("candidate_locked", kind))
            return getattr(candidates, kind)

    class PreviewRepository(module.PreviewJobRepository):
        def recover_expired(self, **_kwargs):
            events.append("preview_recovery")

        def claim_locked(self, job, _seconds):
            events.append(("claim", "preview", job.id))
            return PreviewClaim(job.id, uuid4(), uuid4(), uuid4(), 1, 0, 1000, 1)

    class RenderRepository(module.RenderJobRepository):
        def recover_expired(self, **_kwargs):
            events.append("render_recovery")

        def claim_locked(self, job, _seconds):
            events.append(("claim", "render", job.id))
            return RenderClaim(job.id, uuid4(), uuid4(), uuid4(), 1, 2, "standard", "r05-1")

    monkeypatch.setattr(module, "PreviewJobRepository", PreviewRepository)
    monkeypatch.setattr(module, "RenderJobRepository", RenderRepository)
    worker = WorkerScheduler(settings, Session, Event(), lambda *_: None, lambda *_: None)
    return SimpleNamespace(worker=worker, candidates=candidates, events=events, now=now)


@pytest.mark.parametrize("older", ["preview", "render"])
def test_oldest_eligible_queue_wins_without_starvation(selection, older):
    context = selection
    for kind in ("preview", "render"):
        setattr(
            context.candidates,
            kind,
            SimpleNamespace(
                id=uuid4(), created_at=context.now - timedelta(seconds=int(kind == older))
            ),
        )
    claim = context.worker.claim_next()
    assert claim.id == getattr(context.candidates, older).id
    assert [
        event for event in context.events if isinstance(event, tuple) and event[0] == "claim"
    ] == [("claim", older, claim.id)]
    assert context.events[-2:] == ["transaction_commit", "session_close"]
    assert context.events.index(("candidate_locked", "preview")) < context.events.index(
        ("candidate_locked", "render")
    )


@pytest.mark.parametrize("present", ["preview", "render", None])
def test_single_queue_and_empty_queue(selection, present):
    context = selection
    if present:
        setattr(context.candidates, present, SimpleNamespace(id=uuid4(), created_at=context.now))
    claim = context.worker.claim_next()
    assert (claim is not None) == (present is not None)


def test_equal_timestamps_use_uuid_order(selection):
    context = selection
    context.candidates.preview = SimpleNamespace(id=UUID(int=2), created_at=context.now)
    context.candidates.render = SimpleNamespace(id=UUID(int=1), created_at=context.now)
    assert isinstance(context.worker.claim_next(), RenderClaim)


def test_no_processing_before_claim_commit(selection):
    context = selection
    context.candidates.render = SimpleNamespace(id=uuid4(), created_at=context.now)
    records = []

    def process(claim):
        assert context.events[-2:] == ["transaction_commit", "session_close"]
        records.append(claim.id)

    context.worker.renders = SimpleNamespace(process_claim=process)
    context.worker.previews = SimpleNamespace(process_claim=lambda *_: pytest.fail("wrong queue"))
    assert context.worker.run_one()
    assert records == [context.candidates.render.id]


def test_stop_prevents_claim_and_idle_wait_is_bounded(selection, monkeypatch):
    context = selection
    context.worker.stopped.set()
    assert context.worker.claim_next() is None
    assert context.events == []
    calls = []

    class StopAfterWait:
        def is_set(self):
            return bool(calls)

        def wait(self, seconds):
            calls.append(seconds)

    context.worker.stopped = StopAfterWait()
    monkeypatch.setattr(context.worker, "run_one", lambda: False)
    context.worker.run()
    assert calls == [
        min(
            context.worker.settings.preview_worker_poll_seconds,
            context.worker.settings.render_poll_seconds,
        )
    ]


def test_queue_failure_is_sanitized_and_no_processor_starts(selection, monkeypatch, caplog):
    context = selection

    def unavailable():
        raise SQLAlchemyError("password /private/db")

    monkeypatch.setattr(context.worker, "claim_next", unavailable)
    assert not context.worker.run_one()
    assert "worker_queue_database_unavailable" in caplog.text
    assert "password" not in caplog.text and "/private" not in caplog.text


def test_uncertain_claim_commit_never_starts_processor(selection):
    context = selection

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        @contextmanager
        def begin(self):
            yield
            raise SQLAlchemyError("commit reply lost")

        def scalar(self, query):
            return (
                None
                if query.column_descriptions[0]["entity"] is PreviewJob
                else context.candidates.render
            )

    context.candidates.render = SimpleNamespace(id=uuid4(), created_at=context.now)
    context.worker.sessions = Session
    context.worker.renders = SimpleNamespace(
        process_claim=lambda *_: pytest.fail("cannot start after uncertain commit")
    )
    assert not context.worker.run_one()
