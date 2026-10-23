"""PostgreSQL preview claims and lease-fenced state changes.

The caller owns the short transaction and must commit a claim before processing.
The optional project scope isolates operational QA from unrelated jobs.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, exists, func, select, update
from sqlalchemy.orm import Session

from app.models.preview_job import PreviewJob
from app.models.project import Project

RunningStage = Literal["preparing", "encoding", "validating", "publishing"]
RUNNING_STAGES = {"preparing", "encoding", "validating", "publishing"}
LIVE_STATUSES = ("queued", "running", "completed")
MAX_ATTEMPTS = 2


def database_now():
    """Use the wall clock, including after a transaction has waited for a lock."""
    return func.clock_timestamp(type_=DateTime(timezone=True))


@dataclass(frozen=True)
class PreviewClaim:
    id: UUID
    project_id: UUID
    edit_spec_revision_id: UUID
    claim_token: UUID
    attempts: int
    preview_start_ms: int
    duration_ms: int
    profile_version: int


class PreviewJobRepository:
    def __init__(self, session: Session, *, max_attempts: int = MAX_ATTEMPTS) -> None:
        if type(max_attempts) is not int or not 1 <= max_attempts <= MAX_ATTEMPTS:
            raise ValueError("invalid_preview_attempt_limit")
        self.session = session
        self.max_attempts = max_attempts

    def add(self, job: PreviewJob) -> None:
        self.session.add(job)

    def get_for_project(self, project_id: UUID, job_id: UUID) -> PreviewJob | None:
        return self.session.scalar(
            select(PreviewJob).where(PreviewJob.id == job_id, PreviewJob.project_id == project_id)
        )

    def get_equivalent(self, project_id: UUID, idempotency_key: str) -> PreviewJob | None:
        return self.session.scalar(
            select(PreviewJob).where(
                PreviewJob.project_id == project_id,
                PreviewJob.idempotency_key == idempotency_key,
                PreviewJob.status.in_(LIVE_STATUSES),
            )
        )

    def count_active(self, project_id: UUID) -> int:
        return int(
            self.session.scalar(
                select(func.count())
                .select_from(PreviewJob)
                .where(
                    PreviewJob.project_id == project_id,
                    PreviewJob.status.in_(("queued", "running")),
                )
            )
            or 0
        )

    @staticmethod
    def claim_query(project_id: UUID | None = None, max_attempts: int = MAX_ATTEMPTS):
        query = (
            select(PreviewJob)
            .join(Project, Project.id == PreviewJob.project_id)
            .where(
                Project.deleted_at.is_(None),
                PreviewJob.status == "queued",
                PreviewJob.attempts < max_attempts,
            )
            .order_by(PreviewJob.created_at, PreviewJob.id)
            .limit(1)
            .with_for_update(of=PreviewJob, skip_locked=True)
        )
        if project_id is not None:
            query = query.where(PreviewJob.project_id == project_id)
        return query

    def claim_next(
        self, lease_seconds: float, *, project_id: UUID | None = None
    ) -> PreviewClaim | None:
        job = self.session.scalar(self.claim_query(project_id, self.max_attempts))
        if job is None:
            return None
        return self.claim_locked(job, lease_seconds)

    def claim_locked(self, job: PreviewJob, lease_seconds: float) -> PreviewClaim:
        """Claim the eligible row already locked by this transaction's scheduler."""
        if job.status != "queued" or job.attempts >= self.max_attempts:
            raise ValueError("invalid_preview_claim_candidate")
        token = uuid4()
        attempt = job.attempts + 1
        self.session.execute(
            update(PreviewJob)
            .where(PreviewJob.id == job.id, PreviewJob.status == "queued")
            .values(
                status="running",
                stage="preparing",
                attempts=attempt,
                claim_token=token,
                heartbeat_at=database_now(),
                lease_expires_at=database_now() + timedelta(seconds=lease_seconds),
                started_at=func.coalesce(PreviewJob.started_at, database_now()),
                finished_at=None,
                error_code=None,
            )
            .execution_options(synchronize_session=False)
        )
        return PreviewClaim(
            job.id,
            job.project_id,
            job.edit_spec_revision_id,
            token,
            attempt,
            job.preview_start_ms,
            job.duration_ms,
            job.profile_version,
        )

    @staticmethod
    def ownership(claim: PreviewClaim):
        return (
            PreviewJob.id == claim.id,
            PreviewJob.project_id == claim.project_id,
            PreviewJob.claim_token == claim.claim_token,
            PreviewJob.status == "running",
            PreviewJob.lease_expires_at > database_now(),
            exists().where(Project.id == PreviewJob.project_id, Project.deleted_at.is_(None)),
        )

    def heartbeat(self, claim: PreviewClaim, lease_seconds: float) -> bool:
        return bool(
            self.session.execute(
                update(PreviewJob)
                .where(*self.ownership(claim))
                .values(
                    heartbeat_at=database_now(),
                    lease_expires_at=database_now() + timedelta(seconds=lease_seconds),
                )
                .execution_options(synchronize_session=False)
            ).rowcount
        )

    def set_stage(self, claim: PreviewClaim, stage: RunningStage, lease_seconds: float) -> bool:
        if stage not in RUNNING_STAGES:
            raise ValueError("invalid_preview_stage")
        return bool(
            self.session.execute(
                update(PreviewJob)
                .where(*self.ownership(claim))
                .values(
                    stage=stage,
                    heartbeat_at=database_now(),
                    lease_expires_at=database_now() + timedelta(seconds=lease_seconds),
                )
                .execution_options(synchronize_session=False)
            ).rowcount
        )

    def fail_owned(self, claim: PreviewClaim, error_code: str, *, retry: bool = False) -> bool:
        if not error_code.startswith("preview_") or not error_code.replace("_", "").isalnum():
            raise ValueError("invalid_preview_error_code")
        if len(error_code) > 64:
            raise ValueError("invalid_preview_error_code")
        requeue = retry and claim.attempts < self.max_attempts
        return bool(
            self.session.execute(
                update(PreviewJob)
                .where(*self.ownership(claim))
                .values(
                    status="queued" if requeue else "failed",
                    stage="queued" if requeue else "failed",
                    error_code=None if requeue else error_code,
                    claim_token=None,
                    lease_expires_at=None,
                    finished_at=None if requeue else database_now(),
                )
                .execution_options(synchronize_session=False)
            ).rowcount
        )

    def lock_owned_for_publish(self, claim: PreviewClaim) -> PreviewJob | None:
        """Caller must lock the active Project first, then keep this transaction short."""
        job = self.session.scalar(
            select(PreviewJob)
            .where(*self.ownership(claim))
            .with_for_update(of=PreviewJob)
            .execution_options(populate_existing=True)
        )
        # A lock wait can outlive the lease even without changing the row. Use
        # a separate wall-clock statement after obtaining the lock.
        return job if job is not None and self.is_owned(claim) else None

    def is_owned(self, claim: PreviewClaim) -> bool:
        return bool(self.session.scalar(select(exists().where(*self.ownership(claim)))))

    def recover_expired(self, *, project_id: UUID | None = None, limit: int = 25) -> int:
        """Recover orphans and terminalize deleted/exhausted work.

        Recovery intentionally tests an expired lease instead of accepting an old
        claim token. Row locks and a fresh database wall clock serialize recovery.
        """
        query = (
            select(PreviewJob, Project.deleted_at)
            .join(Project, Project.id == PreviewJob.project_id)
            .where(
                PreviewJob.status.in_(("queued", "running")),
                ((PreviewJob.status == "running") & (PreviewJob.lease_expires_at <= database_now()))
                | ((PreviewJob.status == "queued") & (PreviewJob.attempts >= self.max_attempts))
                | Project.deleted_at.is_not(None),
            )
            .order_by(PreviewJob.created_at, PreviewJob.id)
            .limit(limit)
            .with_for_update(of=PreviewJob, skip_locked=True)
        )
        if project_id is not None:
            query = query.where(PreviewJob.project_id == project_id)
        rows = list(self.session.execute(query))
        for job, deleted_at in rows:
            retry = deleted_at is None and job.attempts < self.max_attempts
            self.session.execute(
                update(PreviewJob)
                .where(PreviewJob.id == job.id, PreviewJob.status == job.status)
                .values(
                    status="queued" if retry else "failed",
                    stage="queued" if retry else "failed",
                    claim_token=None,
                    lease_expires_at=None,
                    finished_at=None if retry else database_now(),
                    error_code=None
                    if retry
                    else "preview_project_unavailable"
                    if deleted_at is not None
                    else "preview_attempts_exhausted",
                )
                .execution_options(synchronize_session=False)
            )
        return len(rows)
