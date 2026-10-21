"""One durable preview at a time, using an explicitly supplied real processor."""

import logging
from collections.abc import Callable
from threading import Event
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.repositories.preview_jobs import PreviewClaim, PreviewJobRepository, RunningStage
from app.worker.supervisor import LeaseLost, PreviewProcessError, ProcessStopped

logger = logging.getLogger("edivo.worker")
SessionFactory = Callable[[], Session]


def claim_log_fields(claim: PreviewClaim) -> dict:
    return {
        "job_id": str(claim.id),
        "project_id": str(claim.project_id),
        "attempts": claim.attempts,
    }


class LeaseController:
    def __init__(
        self, settings: Settings, sessions: SessionFactory, claim: PreviewClaim, stopped: Event
    ) -> None:
        self.settings, self.sessions, self.claim, self.stopped = settings, sessions, claim, stopped

    def heartbeat(self) -> bool:
        if self.stopped.is_set():
            return False
        try:
            with self.sessions() as session, session.begin():
                return PreviewJobRepository(session).heartbeat(
                    self.claim, self.settings.preview_lease_seconds
                )
        except SQLAlchemyError:
            logger.warning(
                "preview_heartbeat_database_unavailable",
                extra={
                    **claim_log_fields(self.claim),
                    "error_code": "preview_database_unavailable",
                },
            )
            return False

    def set_stage(self, stage: RunningStage) -> None:
        if self.stopped.is_set():
            raise ProcessStopped()
        try:
            with self.sessions() as session, session.begin():
                updated = PreviewJobRepository(session).set_stage(
                    self.claim, stage, self.settings.preview_lease_seconds
                )
        except SQLAlchemyError:
            raise LeaseLost() from None
        if not updated:
            raise LeaseLost()
        logger.info(
            "preview_job_stage_changed", extra={**claim_log_fields(self.claim), "stage": stage}
        )


class PreviewWorker:
    def __init__(
        self,
        settings: Settings,
        sessions: SessionFactory,
        stopped: Event,
        processor: Callable[[PreviewClaim, LeaseController], None],
        *,
        project_id: UUID | None = None,
    ) -> None:
        self.settings = settings
        self.sessions = sessions
        self.stopped = stopped
        self.processor = processor
        self.project_id = project_id

    def finish_failure(self, claim: PreviewClaim, code: str, *, retry: bool = False) -> None:
        try:
            with self.sessions() as session, session.begin():
                updated = PreviewJobRepository(
                    session, max_attempts=self.settings.preview_max_attempts
                ).fail_owned(claim, code, retry=retry)
            if updated:
                requeued = retry and claim.attempts < self.settings.preview_max_attempts
                log = logger.info if requeued else logger.warning
                log(
                    "preview_job_requeued" if requeued else "preview_job_failed",
                    extra={
                        **claim_log_fields(claim),
                        "stage": "queued" if requeued else "failed",
                        "error_code": code,
                    },
                )
        except SQLAlchemyError:
            logger.warning(
                "preview_failure_state_database_unavailable",
                extra={**claim_log_fields(claim), "error_code": "preview_database_unavailable"},
            )

    def run_one(self) -> bool:
        if self.stopped.is_set():
            return False
        try:
            with self.sessions() as session, session.begin():
                repository = PreviewJobRepository(
                    session, max_attempts=self.settings.preview_max_attempts
                )
                repository.recover_expired(project_id=self.project_id)
                claim = repository.claim_next(
                    self.settings.preview_lease_seconds, project_id=self.project_id
                )
            # Leaving session.begin commits, including when the response is lost:
            # on commit failure no subprocess starts; expiry reconciles the claim.
        except SQLAlchemyError:
            logger.warning("preview_queue_database_unavailable")
            return False
        if claim is None:
            return False
        logger.info("preview_job_claimed", extra={**claim_log_fields(claim), "stage": "preparing"})
        control = LeaseController(self.settings, self.sessions, claim, self.stopped)
        try:
            self.processor(claim, control)
            # A correct processor has already published/committed completion. If
            # it returned without doing so, fail only its still-owned live claim.
            self.finish_failure(claim, "preview_processing_incomplete")
        except ProcessStopped:
            self.finish_failure(claim, "preview_worker_interrupted", retry=True)
        except LeaseLost:
            logger.warning(
                "preview_lease_lost",
                extra={**claim_log_fields(claim), "error_code": "preview_lease_lost"},
            )
        except PreviewProcessError as error:
            self.finish_failure(claim, error.code)
        except Exception:
            self.finish_failure(claim, "preview_processing_failed")
        return True

    def run(self) -> None:
        while not self.stopped.is_set():
            if not self.run_one():
                self.stopped.wait(self.settings.preview_worker_poll_seconds)
