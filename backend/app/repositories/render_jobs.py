"""Short PostgreSQL transactions for fenced final exports and durable cancellation."""

import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import case, exists, func, select, update
from sqlalchemy.orm import Session

from app.models.project import Project
from app.models.render_job import RenderJob
from app.repositories.preview_jobs import RUNNING_STAGES, RunningStage, database_now

RENDER_ENGINE_VERSION = "r05-1"
ACTIVE_STATUSES = ("queued", "running")
# Stable bigint namespace: ASCII "EDIVOR05". Admission only; never a worker lock.
RENDER_ADMISSION_LOCK_KEY = 0x454449564F523035


@dataclass(frozen=True)
class RenderClaim:
    id: UUID
    project_id: UUID
    edit_spec_revision_id: UUID
    claim_token: UUID
    attempts: int
    max_attempts: int
    profile: str
    render_engine_version: str


class RenderJobRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, job: RenderJob) -> None:
        self.session.add(job)

    def get_for_project(
        self, project_id: UUID, job_id: UUID, *, for_update: bool = False
    ) -> RenderJob | None:
        query = select(RenderJob).where(RenderJob.id == job_id, RenderJob.project_id == project_id)
        if for_update:
            query = query.with_for_update(of=RenderJob).execution_options(populate_existing=True)
        return self.session.scalar(query)

    def get_by_key(self, project_id: UUID, key: str) -> RenderJob | None:
        return self.session.scalar(
            select(RenderJob).where(
                RenderJob.project_id == project_id, RenderJob.idempotency_key == key
            )
        )

    def equivalents(self, project_id: UUID, fingerprint: str) -> list[RenderJob]:
        return list(
            self.session.scalars(
                select(RenderJob)
                .where(
                    RenderJob.project_id == project_id,
                    RenderJob.request_fingerprint == fingerprint,
                    RenderJob.status.in_((*ACTIVE_STATUSES, "completed")),
                )
                .order_by(RenderJob.created_at.desc(), RenderJob.id.desc())
            )
        )

    def list_for_project(
        self, project_id: UUID, limit: int, offset: int, status: str | None = None
    ) -> tuple[list[RenderJob], int]:
        predicates = [RenderJob.project_id == project_id]
        if status is not None:
            predicates.append(RenderJob.status == status)
        total = int(
            self.session.scalar(select(func.count()).select_from(RenderJob).where(*predicates)) or 0
        )
        rows = self.session.scalars(
            select(RenderJob)
            .where(*predicates)
            .order_by(RenderJob.created_at.desc(), RenderJob.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(rows), total

    def count_active(self, project_id: UUID) -> int:
        return int(
            self.session.scalar(
                select(func.count())
                .select_from(RenderJob)
                .where(RenderJob.project_id == project_id, RenderJob.status.in_(ACTIVE_STATUSES))
            )
            or 0
        )

    def lock_global_admission(self) -> None:
        """Serialize new admissions until commit; caller already holds Project.

        SQLite is used only by isolated units, never as concurrency evidence.
        PostgreSQL's transaction lock protects COUNT plus INSERT across projects.
        """
        if self.session.get_bind().dialect.name == "postgresql":
            self.session.execute(select(func.pg_advisory_xact_lock(RENDER_ADMISSION_LOCK_KEY)))

    def count_active_total(self) -> int:
        # Include soft-deleted projects until their active jobs are recovered;
        # hiding them would allow an offline queue to bypass its global bound.
        return int(
            self.session.scalar(
                select(func.count())
                .select_from(RenderJob)
                .where(RenderJob.status.in_(ACTIVE_STATUSES))
            )
            or 0
        )

    @staticmethod
    def claim_query(project_id: UUID | None = None):
        query = (
            select(RenderJob)
            .join(Project, Project.id == RenderJob.project_id)
            .where(
                Project.deleted_at.is_(None),
                RenderJob.status == "queued",
                RenderJob.cancel_requested_at.is_(None),
                RenderJob.attempts < RenderJob.max_attempts,
            )
            .order_by(RenderJob.created_at, RenderJob.id)
            .limit(1)
            .with_for_update(of=RenderJob, skip_locked=True)
        )
        return query if project_id is None else query.where(RenderJob.project_id == project_id)

    def claim_next(
        self, lease_seconds: float, *, project_id: UUID | None = None
    ) -> RenderClaim | None:
        job = self.session.scalar(self.claim_query(project_id))
        if job is None:
            return None
        return self.claim_locked(job, lease_seconds)

    def claim_locked(self, job: RenderJob, lease_seconds: float) -> RenderClaim:
        """Claim the eligible row already locked by this transaction's scheduler."""
        if (
            job.status != "queued"
            or job.cancel_requested_at is not None
            or job.attempts >= job.max_attempts
        ):
            raise ValueError("invalid_render_claim_candidate")
        token, attempts = uuid4(), job.attempts + 1
        self.session.execute(
            update(RenderJob)
            .where(RenderJob.id == job.id, RenderJob.status == "queued")
            .values(
                status="running",
                stage="preparing",
                attempts=attempts,
                claim_token=token,
                lease_expires_at=database_now() + timedelta(seconds=lease_seconds),
                heartbeat_at=database_now(),
                started_at=func.coalesce(RenderJob.started_at, database_now()),
                updated_at=database_now(),
                finished_at=None,
                progress_encoded_ms=None,
                error_code=None,
            )
            .execution_options(synchronize_session=False)
        )
        return RenderClaim(
            job.id,
            job.project_id,
            job.edit_spec_revision_id,
            token,
            attempts,
            job.max_attempts,
            job.profile,
            job.render_engine_version,
        )

    @staticmethod
    def ownership(claim: RenderClaim, *, allow_cancel: bool = False):
        conditions = (
            RenderJob.id == claim.id,
            RenderJob.project_id == claim.project_id,
            RenderJob.claim_token == claim.claim_token,
            RenderJob.attempts == claim.attempts,
            RenderJob.status == "running",
            RenderJob.lease_expires_at > database_now(),
            exists().where(Project.id == RenderJob.project_id, Project.deleted_at.is_(None)),
        )
        return (
            conditions if allow_cancel else (*conditions, RenderJob.cancel_requested_at.is_(None))
        )

    def is_owned(self, claim: RenderClaim, *, allow_cancel: bool = False) -> bool:
        return bool(
            self.session.scalar(
                select(exists().where(*self.ownership(claim, allow_cancel=allow_cancel)))
            )
        )

    def control_state(self, claim: RenderClaim) -> Literal["owned", "cancel_requested", "lost"]:
        row = self.session.execute(
            select(RenderJob.cancel_requested_at).where(*self.ownership(claim, allow_cancel=True))
        ).first()
        return "lost" if row is None else "cancel_requested" if row[0] is not None else "owned"

    def heartbeat(self, claim: RenderClaim, lease_seconds: float) -> bool:
        if self._lock_owned(claim) is None:
            return False
        return bool(
            self.session.execute(
                update(RenderJob)
                .where(*self.ownership(claim))
                .values(
                    heartbeat_at=database_now(),
                    lease_expires_at=database_now() + timedelta(seconds=lease_seconds),
                    updated_at=database_now(),
                )
                .execution_options(synchronize_session=False)
            ).rowcount
        )

    def set_stage(self, claim: RenderClaim, stage: RunningStage, lease_seconds: float) -> bool:
        if stage not in RUNNING_STAGES:
            raise ValueError("invalid_render_stage")
        if self._lock_owned(claim) is None:
            return False
        return bool(
            self.session.execute(
                update(RenderJob)
                .where(*self.ownership(claim))
                .values(
                    stage=stage,
                    heartbeat_at=database_now(),
                    lease_expires_at=database_now() + timedelta(seconds=lease_seconds),
                    updated_at=database_now(),
                )
                .execution_options(synchronize_session=False)
            ).rowcount
        )

    def update_progress(self, claim: RenderClaim, encoded_ms: int) -> bool:
        if type(encoded_ms) is not int or not 0 <= encoded_ms <= 2**63 - 1:
            raise ValueError("invalid_render_progress")
        if self._lock_owned(claim) is None:
            return False
        return bool(
            self.session.execute(
                update(RenderJob)
                .where(*self.ownership(claim), RenderJob.stage == "encoding")
                .values(
                    progress_encoded_ms=case(
                        (func.coalesce(RenderJob.progress_encoded_ms, -1) < encoded_ms, encoded_ms),
                        else_=RenderJob.progress_encoded_ms,
                    ),
                    updated_at=database_now(),
                )
                .execution_options(synchronize_session=False)
            ).rowcount
        )

    def request_cancel(self, job: RenderJob) -> bool:
        """Caller holds Project then Job locks. Return whether worker confirmation is needed."""
        if job.status not in ACTIVE_STATUSES:
            raise ValueError("render_not_cancelable")
        queued = job.status == "queued"
        values = dict(
            cancel_requested_at=func.coalesce(RenderJob.cancel_requested_at, database_now()),
            updated_at=database_now(),
        )
        if queued:
            values.update(
                status="canceled", stage="canceled", finished_at=database_now(), error_code=None
            )
        self.session.execute(
            update(RenderJob)
            .where(RenderJob.id == job.id, RenderJob.status == job.status)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        self.session.refresh(job)
        return not queued

    def cancel_owned(self, claim: RenderClaim) -> bool:
        """Called only after this attempt's subprocess group has stopped."""
        if self._lock_owned(claim, allow_cancel=True) is None:
            return False
        return bool(
            self.session.execute(
                update(RenderJob)
                .where(
                    *self.ownership(claim, allow_cancel=True),
                    RenderJob.cancel_requested_at.is_not(None),
                )
                .values(
                    status="canceled",
                    stage="canceled",
                    claim_token=None,
                    lease_expires_at=None,
                    finished_at=database_now(),
                    updated_at=database_now(),
                    error_code=None,
                )
                .execution_options(synchronize_session=False)
            ).rowcount
        )

    def fail_owned(self, claim: RenderClaim, error_code: str, *, retry: bool = False) -> bool:
        if re.fullmatch(r"render_[a-z0-9_]{1,57}", error_code) is None:
            raise ValueError("invalid_render_error_code")
        if self._lock_owned(claim, allow_cancel=True) is None:
            return False
        requeue = retry and claim.attempts < claim.max_attempts
        canceling = RenderJob.cancel_requested_at.is_not(None)
        return bool(
            self.session.execute(
                update(RenderJob)
                .where(*self.ownership(claim, allow_cancel=True))
                .values(
                    status=case((canceling, "canceled"), else_="queued" if requeue else "failed"),
                    stage=case((canceling, "canceled"), else_="queued" if requeue else "failed"),
                    error_code=case((canceling, None), else_=None if requeue else error_code),
                    claim_token=None,
                    lease_expires_at=None,
                    finished_at=case(
                        (canceling, database_now()), else_=None if requeue else database_now()
                    ),
                    updated_at=database_now(),
                    progress_encoded_ms=None if requeue else RenderJob.progress_encoded_ms,
                )
                .execution_options(synchronize_session=False)
            ).rowcount
        )

    def lock_owned_for_publish(self, claim: RenderClaim) -> RenderJob | None:
        """Caller locks Project first, preserving ordering with saves/deletion/cancel."""
        return self._lock_owned(claim)

    def _lock_owned(self, claim: RenderClaim, *, allow_cancel: bool = False) -> RenderJob | None:
        job = self.session.scalar(
            select(RenderJob)
            .where(*self.ownership(claim, allow_cancel=allow_cancel))
            .with_for_update(of=RenderJob)
            .execution_options(populate_existing=True)
        )
        # A predicate can have been evaluated before waiting for a row lock.
        # Recheck in a fresh statement after that wait, for every owner mutation.
        return job if job is not None and self.is_owned(claim, allow_cancel=allow_cancel) else None

    def recover_expired(self, *, project_id: UUID | None = None, limit: int = 25) -> int:
        query = (
            select(RenderJob, Project.deleted_at)
            .join(Project, Project.id == RenderJob.project_id)
            .where(
                RenderJob.status.in_(ACTIVE_STATUSES),
                ((RenderJob.status == "running") & (RenderJob.lease_expires_at <= database_now()))
                | ((RenderJob.status == "queued") & (RenderJob.attempts >= RenderJob.max_attempts))
                | ((RenderJob.status == "queued") & RenderJob.cancel_requested_at.is_not(None))
                | ((RenderJob.status == "queued") & Project.deleted_at.is_not(None)),
            )
            .order_by(RenderJob.created_at, RenderJob.id)
            .limit(limit)
            .with_for_update(of=RenderJob, skip_locked=True)
        )
        if project_id is not None:
            query = query.where(RenderJob.project_id == project_id)
        rows = list(self.session.execute(query))
        for job, deleted_at in rows:
            canceling = job.cancel_requested_at is not None
            retry = not canceling and deleted_at is None and job.attempts < job.max_attempts
            terminal = "canceled" if canceling else "failed"
            self.session.execute(
                update(RenderJob)
                .where(RenderJob.id == job.id, RenderJob.status == job.status)
                .values(
                    status="queued" if retry else terminal,
                    stage="queued" if retry else terminal,
                    claim_token=None,
                    lease_expires_at=None,
                    finished_at=None if retry else database_now(),
                    updated_at=database_now(),
                    progress_encoded_ms=None if retry else job.progress_encoded_ms,
                    error_code=None
                    if retry or canceling
                    else "render_project_unavailable"
                    if deleted_at is not None
                    else "render_attempts_exhausted",
                )
                .execution_options(synchronize_session=False)
            )
        return len(rows)
