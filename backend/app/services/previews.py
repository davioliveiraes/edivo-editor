"""Short preview requests and private artifacts; encoding belongs to the worker."""

import hashlib
import re
import stat
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.models.edit_spec_revision import EditSpecRevision
from app.models.preview_job import PreviewJob
from app.models.project import utc_now
from app.repositories.preview_jobs import PreviewJobRepository
from app.schemas.edit_spec import EditSpec
from app.schemas.previews import PreviewCreate, PreviewResponse
from app.services.projects import ProjectService
from app.storage.local import CHUNK_BYTES, LocalStorage, OpenedOriginal

PROFILE_VERSION = 1
plan_adapter = TypeAdapter(EditSpec)


class PreviewService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session, self.settings = session, settings
        self.repository = PreviewJobRepository(session)
        self.storage = LocalStorage(settings)

    def _revision(self, project_id: UUID, revision_id: UUID) -> EditSpecRevision:
        revision = self.session.scalar(
            select(EditSpecRevision).where(
                EditSpecRevision.id == revision_id, EditSpecRevision.project_id == project_id
            )
        )
        if revision is None:
            raise AppError(
                404, "edit_spec_not_found", "Revisão salva não encontrada neste projeto."
            )
        return revision

    def _job(self, project_id: UUID, job_id: UUID) -> tuple[PreviewJob, EditSpecRevision]:
        ProjectService(self.session).get(project_id)
        job = self.repository.get_for_project(project_id, job_id)
        if job is None:
            raise AppError(404, "preview_not_found", "Prévia não encontrada neste projeto.")
        return job, self._revision(project_id, job.edit_spec_revision_id)

    def _artifact(self, job: PreviewJob) -> OpenedOriginal:
        opened = None
        try:
            if (
                job.artifact_storage_key is None
                or job.artifact_sha256 is None
                or job.artifact_byte_size is None
                or not 0 < job.artifact_byte_size <= self.settings.preview_max_bytes
            ):
                raise OSError("invalid_preview_artifact")
            opened = self.storage.open_preview(
                job.artifact_storage_key, job.project_id, job.id, job.artifact_byte_size
            )
            if stat.S_IMODE(opened.stat_result.st_mode) != 0o400:
                raise OSError("invalid_preview_permissions")
            checksum = hashlib.sha256()
            while chunk := opened.file.read(CHUNK_BYTES):
                checksum.update(chunk)
            if checksum.hexdigest() != job.artifact_sha256:
                raise OSError("invalid_preview_checksum")
            opened.file.seek(0)
            return opened
        except (AppError, OSError, ValueError):
            if opened is not None:
                opened.file.close()
            raise AppError(
                503, "preview_artifact_unavailable", "O arquivo da prévia está indisponível."
            ) from None

    @staticmethod
    def _response(job: PreviewJob, revision: EditSpecRevision) -> PreviewResponse:
        base = f"/api/v1/projects/{job.project_id}/previews/{job.id}"
        available = job.status == "completed"
        code = job.error_code
        if code is not None and re.fullmatch(r"preview_[a-z0-9_]{1,56}", code) is None:
            code = "preview_processing_failed"
        return PreviewResponse(
            id=job.id,
            project_id=job.project_id,
            revision_id=revision.id,
            revision_number=revision.revision_number,
            preview_start_ms=job.preview_start_ms,
            duration_ms=job.duration_ms,
            status=job.status,
            stage=job.stage,
            attempts=job.attempts,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            error_code=code,
            artifact_available=available,
            checksum_sha256=job.artifact_sha256 if available else None,
            byte_size=job.artifact_byte_size if available else None,
            status_url=base,
            file_url=f"{base}/file" if available else None,
        )

    def create(self, project_id: UUID, data: PreviewCreate) -> tuple[PreviewResponse, bool]:
        # The same project lock serializes saves, deletion and idempotent enqueue.
        ProjectService(self.session).get(project_id, for_update=True)
        revision = self._revision(project_id, data.edit_spec_revision_id)
        try:
            plan = plan_adapter.validate_python(revision.spec_json)
        except ValidationError:
            raise AppError(
                503, "preview_revision_unavailable", "Não foi possível consultar a edição salva."
            ) from None
        if not plan.trim.start_ms <= data.preview_start_ms < plan.trim.end_ms:
            raise AppError(
                422, "invalid_preview_window", "A janela deve estar dentro do trecho salvo."
            )
        duration = min(data.duration_ms, plan.trim.end_ms - data.preview_start_ms)
        key = hashlib.sha256(
            f"{PROFILE_VERSION}:{revision.id}:{data.preview_start_ms}:{duration}".encode("ascii")
        ).hexdigest()
        equivalent = self.repository.get_equivalent(project_id, key)
        if equivalent is not None:
            if equivalent.status != "completed":
                response = self._response(equivalent, revision)
                self.session.commit()
                return response, False
            try:
                self._artifact(equivalent).file.close()
            except AppError:
                # Preserve the unavailable artifact and its audit row. An explicit
                # request may create a new UUID; it never replaces the old file.
                equivalent.status = equivalent.stage = "failed"
                equivalent.error_code = "preview_artifact_unavailable"
                equivalent.finished_at = utc_now()
                self.session.flush()
            else:
                response = self._response(equivalent, revision)
                self.session.commit()
                return response, True
        if self.repository.count_active(project_id) >= self.settings.preview_max_active_per_project:
            raise AppError(
                409,
                "preview_active_limit",
                "Este projeto atingiu o limite de prévias na fila ou em execução.",
            )
        job = PreviewJob(
            id=uuid4(),
            project_id=project_id,
            edit_spec_revision_id=revision.id,
            preview_start_ms=data.preview_start_ms,
            duration_ms=duration,
            profile_version=PROFILE_VERSION,
            idempotency_key=key,
        )
        self.repository.add(job)
        self.session.flush()
        response = self._response(job, revision)
        self.session.commit()
        return response, False

    def get(self, project_id: UUID, job_id: UUID) -> PreviewResponse:
        job, revision = self._job(project_id, job_id)
        if job.status == "completed":
            self._artifact(job).file.close()
        return self._response(job, revision)

    def open_file(self, project_id: UUID, job_id: UUID) -> tuple[OpenedOriginal, str]:
        job, _revision = self._job(project_id, job_id)
        if job.status != "completed":
            raise AppError(
                409, "preview_not_ready", "A prévia ainda não está disponível para reprodução."
            )
        return self._artifact(job), job.artifact_sha256
