"""One serial worker loop and one short FIFO claim transaction across both queues."""

import logging
from collections.abc import Callable
from threading import Event
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.repositories.preview_jobs import PreviewClaim, PreviewJobRepository
from app.repositories.render_jobs import RenderClaim, RenderJobRepository
from app.worker.preview_worker import PreviewWorker, SessionFactory
from app.worker.render_worker import RenderWorker

logger = logging.getLogger("edivo.worker")


class WorkerScheduler:
    def __init__(
        self,
        settings: Settings,
        sessions: SessionFactory,
        stopped: Event,
        preview_processor: Callable,
        render_processor: Callable,
        *,
        project_id: UUID | None = None,
    ) -> None:
        self.settings, self.sessions, self.stopped = settings, sessions, stopped
        self.project_id = project_id
        self.previews = PreviewWorker(settings, sessions, stopped, preview_processor)
        self.renders = RenderWorker(settings, sessions, stopped, render_processor)

    def claim_next(self) -> PreviewClaim | RenderClaim | None:
        """Lock both eligible candidates in a fixed order and claim exactly one.

        The unselected candidate is released by the same short commit. SKIP
        LOCKED lets another worker select other rows without waiting. FIFO is
        between currently eligible/unlocked candidates, with no preemption.
        """
        if self.stopped.is_set():
            return None
        with self.sessions() as session, session.begin():
            previews = PreviewJobRepository(
                session, max_attempts=self.settings.preview_max_attempts
            )
            renders = RenderJobRepository(session)
            previews.recover_expired(project_id=self.project_id)
            renders.recover_expired(project_id=self.project_id)
            preview = session.scalar(
                previews.claim_query(self.project_id, self.settings.preview_max_attempts)
            )
            render = session.scalar(renders.claim_query(self.project_id))
            if preview is None and render is None:
                return None
            if render is None or (
                preview is not None
                and (preview.created_at, preview.id) <= (render.created_at, render.id)
            ):
                claim = previews.claim_locked(preview, self.settings.preview_lease_seconds)
            else:
                claim = renders.claim_locked(render, self.settings.render_lease_seconds)
        # No processor starts until the claim commit has returned successfully.
        return claim

    def run_one(self) -> bool:
        try:
            claim = self.claim_next()
        except SQLAlchemyError:
            logger.warning("worker_queue_database_unavailable")
            return False
        if claim is None:
            return False
        if isinstance(claim, PreviewClaim):
            self.previews.process_claim(claim)
        else:
            self.renders.process_claim(claim)
        return True

    def run(self) -> None:
        while not self.stopped.is_set():
            if not self.run_one():
                self.stopped.wait(
                    min(
                        self.settings.preview_worker_poll_seconds, self.settings.render_poll_seconds
                    )
                )
