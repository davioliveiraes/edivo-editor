"""Export control units; real PostgreSQL/process evidence is recorded separately."""

from contextlib import contextmanager
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.repositories.render_jobs import RenderClaim
from app.worker import render_worker as module
from app.worker.render_worker import RenderLeaseController, RenderWorker
from app.worker.supervisor import LeaseLost, PreviewProcessError, ProcessStopped, RenderCanceled


def claim():
    return RenderClaim(uuid4(), uuid4(), uuid4(), uuid4(), 1, 2, "standard", "r05-1")


@pytest.fixture
def orchestration(settings, monkeypatch):
    events = []
    owner = claim()
    state = SimpleNamespace(value="owned", status="running", stopped_child=True)

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

        def scalar(self, _query):
            return state.status

    class Repository:
        def __init__(self, _session):
            pass

        def control_state(self, received):
            assert received == owner
            return state.value

        def heartbeat(self, received, _seconds):
            assert received == owner
            events.append("heartbeat")
            return state.value == "owned"

        def set_stage(self, received, stage, _seconds):
            assert received == owner
            events.append(("stage", stage))
            return state.value == "owned"

        def update_progress(self, received, value):
            assert received == owner
            events.append(("progress", value))
            return state.value == "owned"

        def fail_owned(self, received, code, *, retry=False):
            assert received == owner
            assert state.stopped_child
            events.append(("failure", code, retry))
            state.status = "queued" if retry else "failed"
            return state.value == "owned"

        def cancel_owned(self, received):
            assert received == owner
            assert state.stopped_child
            events.append("canceled_after_child_stopped")
            return state.value == "cancel_requested"

    monkeypatch.setattr(module, "RenderJobRepository", Repository)
    return SimpleNamespace(
        settings=settings, sessions=Session, owner=owner, events=events, state=state
    )


@pytest.mark.parametrize("state,error", [("cancel_requested", RenderCanceled), ("lost", LeaseLost)])
def test_control_distinguishes_cancellation_and_lost_lease(orchestration, state, error):
    context = orchestration
    context.state.value = state
    controller = RenderLeaseController(context.settings, context.sessions, context.owner, Event())
    with pytest.raises(error):
        controller.control()
    with pytest.raises(error):
        controller.heartbeat()
    with pytest.raises(error):
        controller.set_stage("encoding")
    with pytest.raises(error):
        controller.set_progress(300)


def test_progress_is_throttled_monotonic_and_flushed_before_validation(orchestration, monkeypatch):
    context = orchestration
    clock = SimpleNamespace(value=10.0)
    monkeypatch.setattr(module, "monotonic", lambda: clock.value)
    controller = RenderLeaseController(context.settings, context.sessions, context.owner, Event())
    controller.set_stage("encoding")
    controller.set_progress(100)
    controller.set_progress(90)
    controller.set_progress(600)
    assert [
        event for event in context.events if isinstance(event, tuple) and event[0] == "progress"
    ] == [("progress", 100)]
    controller.set_stage("validating")
    assert ("progress", 600) in context.events
    assert context.events.index(("progress", 600)) < context.events.index(("stage", "validating"))
    controller.set_stage("publishing")
    assert context.events.count(("progress", 600)) == 1


@pytest.mark.parametrize("value", [-1, True, "25", 2.5, 2**63])
def test_invalid_progress_is_not_persisted(orchestration, value):
    context = orchestration
    controller = RenderLeaseController(context.settings, context.sessions, context.owner, Event())
    with pytest.raises(PreviewProcessError, match="render_process_output_invalid"):
        controller.set_progress(value)
    assert context.events == []


def test_control_database_failure_never_exposes_details(settings, caplog):
    def unavailable():
        raise SQLAlchemyError("password /private/database")

    controller = RenderLeaseController(settings, unavailable, claim(), Event())
    assert not controller.heartbeat()
    for action in [controller.control, lambda: controller.set_stage("encoding")]:
        with pytest.raises(LeaseLost):
            action()
    assert "password" not in caplog.text and "/private" not in caplog.text


@pytest.mark.parametrize(
    "error,code,retry",
    [
        (ProcessStopped(), "render_worker_interrupted", True),
        (PreviewProcessError("render_timeout"), "render_timeout", False),
        (
            PreviewProcessError("preview_source_integrity_failed"),
            "render_source_integrity_failed",
            False,
        ),
        (RuntimeError("password /private/source"), "render_processing_failed", False),
    ],
)
def test_worker_failures_are_sanitized_and_interruption_is_retryable(
    orchestration, error, code, retry, caplog
):
    context = orchestration

    def processor(*_args):
        raise error

    RenderWorker(context.settings, context.sessions, Event(), processor).process_claim(
        context.owner
    )
    assert ("failure", code, retry) in context.events
    assert "password" not in caplog.text and "/private" not in caplog.text


def test_cancel_finalized_only_after_processor_unwinds(orchestration):
    context = orchestration
    context.state.stopped_child = False

    def processor(*_args):
        try:
            context.state.value = "cancel_requested"
            raise RenderCanceled()
        finally:
            context.state.stopped_child = True
            context.events.append("processor_and_children_stopped")

    RenderWorker(context.settings, context.sessions, Event(), processor).process_claim(
        context.owner
    )
    assert context.events.index("processor_and_children_stopped") < context.events.index(
        "canceled_after_child_stopped"
    )
    assert not any(isinstance(item, tuple) and item[0] == "failure" for item in context.events)


def test_lost_lease_cannot_mutate_successor(orchestration):
    context = orchestration

    def processor(*_args):
        raise LeaseLost()

    RenderWorker(context.settings, context.sessions, Event(), processor).process_claim(
        context.owner
    )
    assert context.events == []


def test_return_without_completion_is_failed(orchestration):
    context = orchestration
    RenderWorker(context.settings, context.sessions, Event(), lambda *_: None).process_claim(
        context.owner
    )
    assert ("failure", "render_processing_incomplete", False) in context.events


def test_stop_between_claim_commit_and_execution_never_starts_processor(orchestration):
    context = orchestration
    stopped = Event()
    stopped.set()

    def processor(*_args):
        pytest.fail("stopped worker cannot start another process")

    RenderWorker(context.settings, context.sessions, stopped, processor).process_claim(
        context.owner
    )
    assert ("failure", "render_worker_interrupted", True) in context.events


def test_stage_success_event_occurs_only_after_commit(orchestration, monkeypatch):
    context = orchestration
    records = []

    def logged(message, *, extra):
        assert context.events[-2:] == ["transaction_commit", "session_close"]
        records.append((message, extra))

    monkeypatch.setattr(module.logger, "info", logged)
    control = RenderLeaseController(context.settings, context.sessions, context.owner, Event())
    control.set_stage("encoding")
    assert records[0][0] == "render_job_stage_changed"
    assert records[0][1]["job_id"] == str(context.owner.id)
