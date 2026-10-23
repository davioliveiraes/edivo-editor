"""Fenced export execution; queue selection belongs to the shared scheduler."""

import logging
from collections.abc import Callable
from threading import Event
from time import monotonic

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.models.render_job import RenderJob
from app.repositories.preview_jobs import RunningStage
from app.repositories.render_jobs import RenderClaim, RenderJobRepository
from app.worker.preview_worker import SessionFactory
from app.worker.supervisor import (
    LeaseLost,
    PreviewProcessError,
    ProcessStopped,
    RenderCanceled,
)

logger = logging.getLogger("edivo.worker")


def render_log_fields(claim: RenderClaim) -> dict:
    return {
        "job_id": str(claim.id),
        "project_id": str(claim.project_id),
        "attempts": claim.attempts,
    }


class RenderLeaseController:
    """Short transactions only; never keep a session open during FFmpeg."""

    def __init__(
        self, settings: Settings, sessions: SessionFactory, claim: RenderClaim, stopped: Event
    ) -> None:
        self.settings, self.sessions, self.claim, self.stopped = settings, sessions, claim, stopped
        self.stage: RunningStage = "preparing"
        self._observed_progress: int | None = None
        self._persisted_progress: int | None = None
        self._next_progress_at = 0.0

    def _require_owned(self, repository: RenderJobRepository) -> None:
        state = repository.control_state(self.claim)
        if state == "cancel_requested":
            raise RenderCanceled()
        if state != "owned":
            raise LeaseLost()

    def control(self) -> None:
        if self.stopped.is_set():
            raise ProcessStopped()
        try:
            with self.sessions() as session:
                self._require_owned(RenderJobRepository(session))
        except SQLAlchemyError:
            raise LeaseLost() from None

    def heartbeat(self) -> bool:
        if self.stopped.is_set():
            return False
        try:
            with self.sessions() as session, session.begin():
                repository = RenderJobRepository(session)
                updated = repository.heartbeat(self.claim, self.settings.render_lease_seconds)
                if not updated:
                    # Cancellation may commit between the control poll and this
                    # heartbeat. Preserve its meaning instead of losing the lease.
                    self._require_owned(repository)
                return updated
        except SQLAlchemyError:
            logger.warning(
                "render_heartbeat_database_unavailable",
                extra={
                    **render_log_fields(self.claim),
                    "error_code": "render_database_unavailable",
                },
            )
            return False

    def _flush_progress(self) -> None:
        value = self._observed_progress
        if value is None or value == self._persisted_progress:
            return
        if self.stopped.is_set():
            raise ProcessStopped()
        try:
            with self.sessions() as session, session.begin():
                repository = RenderJobRepository(session)
                if not repository.update_progress(self.claim, value):
                    self._require_owned(repository)
                    raise LeaseLost()
        except SQLAlchemyError:
            raise LeaseLost() from None
        self._persisted_progress = value
        self._next_progress_at = monotonic() + 1.0

    def set_progress(self, encoded_ms: int) -> None:
        if type(encoded_ms) is not int or not 0 <= encoded_ms <= 2**63 - 1:
            raise PreviewProcessError("render_process_output_invalid")
        self._observed_progress = max(self._observed_progress or 0, encoded_ms)
        if monotonic() >= self._next_progress_at:
            self._flush_progress()

    def set_stage(self, stage: RunningStage) -> None:
        if self.stopped.is_set():
            raise ProcessStopped()
        if self.stage == "encoding" and stage != "encoding":
            # Persist the final real observation even for a sub-second encode.
            self._flush_progress()
        try:
            with self.sessions() as session, session.begin():
                repository = RenderJobRepository(session)
                if not repository.set_stage(self.claim, stage, self.settings.render_lease_seconds):
                    self._require_owned(repository)
                    raise LeaseLost()
        except SQLAlchemyError:
            raise LeaseLost() from None
        self.stage = stage
        logger.info(
            "render_job_stage_changed", extra={**render_log_fields(self.claim), "stage": stage}
        )


class RenderWorker:
    """Execute an already-committed claim; this class owns no competing queue loop."""

    def __init__(
        self,
        settings: Settings,
        sessions: SessionFactory,
        stopped: Event,
        processor: Callable[[RenderClaim, RenderLeaseController], None],
    ) -> None:
        self.settings, self.sessions, self.stopped = settings, sessions, stopped
        self.processor = processor

    def finish_failure(self, claim: RenderClaim, code: str, *, retry: bool = False) -> None:
        try:
            with self.sessions() as session, session.begin():
                updated = RenderJobRepository(session).fail_owned(claim, code, retry=retry)
                status = (
                    session.scalar(select(RenderJob.status).where(RenderJob.id == claim.id))
                    if updated
                    else None
                )
            if updated:
                log = logger.warning if status == "failed" else logger.info
                log(
                    "render_job_requeued" if status == "queued" else f"render_job_{status}",
                    extra={
                        **render_log_fields(claim),
                        "stage": status,
                        "error_code": code if status == "failed" else None,
                    },
                )
        except SQLAlchemyError:
            logger.warning(
                "render_failure_state_database_unavailable",
                extra={**render_log_fields(claim), "error_code": "render_database_unavailable"},
            )

    def finish_cancellation(self, claim: RenderClaim) -> None:
        # The processor has unwound: ProcessRunner's finally has stopped/waited
        # its own process group, and the attempt's TemporaryDirectory is closed.
        try:
            with self.sessions() as session, session.begin():
                updated = RenderJobRepository(session).cancel_owned(claim)
            if updated:
                logger.info(
                    "render_job_canceled",
                    extra={**render_log_fields(claim), "stage": "canceled"},
                )
        except SQLAlchemyError:
            logger.warning(
                "render_cancel_state_database_unavailable",
                extra={**render_log_fields(claim), "error_code": "render_database_unavailable"},
            )

    def process_claim(self, claim: RenderClaim) -> None:
        logger.info("render_job_claimed", extra={**render_log_fields(claim), "stage": "preparing"})
        control = RenderLeaseController(self.settings, self.sessions, claim, self.stopped)
        try:
            if self.stopped.is_set():
                raise ProcessStopped()
            self.processor(claim, control)
            self.finish_failure(claim, "render_processing_incomplete")
        except RenderCanceled:
            self.finish_cancellation(claim)
        except ProcessStopped:
            self.finish_failure(claim, "render_worker_interrupted", retry=True)
        except LeaseLost:
            logger.warning(
                "render_lease_lost",
                extra={**render_log_fields(claim), "error_code": "render_lease_lost"},
            )
        except PreviewProcessError as error:
            code = error.code
            if code.startswith("preview_"):
                code = "render_" + code.removeprefix("preview_")
            self.finish_failure(claim, code)
        except Exception:
            self.finish_failure(claim, "render_processing_failed")
