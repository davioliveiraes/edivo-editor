"""Final-export requests/history; only the independent worker performs encoding."""

import hashlib
import os
import re
import stat
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.models.audio_asset import AudioAsset
from app.models.edit_spec_revision import EditSpecRevision
from app.models.media_asset import MediaAsset
from app.models.render_job import RenderJob
from app.repositories.render_jobs import RENDER_ENGINE_VERSION, RenderJobRepository
from app.schemas.edit_spec import EditSpec, EditSpecV2
from app.schemas.renders import RenderCreate, RenderPage, RenderResponse
from app.services.projects import ProjectService
from app.storage.local import CHUNK_BYTES, LocalStorage, OpenedOriginal

PLAN_ADAPTER = TypeAdapter(EditSpec)


class RenderService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session, self.settings = session, settings
        self.repository = RenderJobRepository(session)
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

    @staticmethod
    def _plan(revision: EditSpecRevision):
        try:
            plan = PLAN_ADAPTER.validate_python(revision.spec_json)
            if (
                plan.schema_version != revision.schema_version
                or plan.source_media_id != revision.source_media_id
            ):
                raise ValueError
            return plan
        except (ValidationError, ValueError):
            raise AppError(
                503, "render_revision_unavailable", "Não foi possível consultar a edição salva."
            ) from None

    def _validate_inputs(self, project_id: UUID, revision: EditSpecRevision) -> None:
        plan = self._plan(revision)
        source = self.session.get(MediaAsset, plan.source_media_id)
        if (
            source is None
            or source.project_id != project_id
            or source.kind != "source_video"
            or source.mime_detected != "video/mp4"
            or plan.trim.end_ms > source.duration_ms
            or plan.trim.end_ms - plan.trim.start_ms < min(100, source.duration_ms)
        ):
            raise AppError(503, "render_source_unavailable", "A fonte da edição está indisponível.")
        opened = None
        try:
            opened = self.storage.open_original(
                source.storage_key, project_id, source.id, source.byte_size
            )
            if stat.S_IMODE(opened.stat_result.st_mode) != 0o400:
                raise OSError
        except (AppError, OSError, ValueError):
            raise AppError(
                503, "render_source_unavailable", "A fonte da edição está indisponível."
            ) from None
        finally:
            if opened is not None:
                opened.file.close()
        if isinstance(plan, EditSpecV2) and plan.audio.background_audio_id is not None:
            audio = self.session.get(AudioAsset, plan.audio.background_audio_id)
            if audio is None or audio.project_id != project_id or audio.codec != "mp3":
                raise AppError(503, "render_audio_unavailable", "A música salva está indisponível.")
            opened = None
            try:
                opened = self.storage.open_audio(
                    audio.storage_key, project_id, audio.id, audio.byte_size
                )
                if stat.S_IMODE(opened.stat_result.st_mode) != 0o400:
                    raise OSError
            except (AppError, OSError, ValueError):
                raise AppError(
                    503, "render_audio_unavailable", "A música salva está indisponível."
                ) from None
            finally:
                if opened is not None:
                    opened.file.close()

    def _job(self, project_id: UUID, render_id: UUID, *, for_update=False):
        ProjectService(self.session).get(project_id, for_update=for_update)
        job = self.repository.get_for_project(project_id, render_id, for_update=for_update)
        if job is None:
            raise AppError(404, "render_not_found", "Exportação não encontrada neste projeto.")
        return job, self._revision(project_id, job.edit_spec_revision_id)

    def _artifact(self, job: RenderJob, *, verify_checksum=True) -> OpenedOriginal:
        opened = None
        try:
            if (
                job.artifact_storage_key is None
                or job.artifact_sha256 is None
                or job.artifact_byte_size is None
                or not 0 < job.artifact_byte_size <= self.settings.render_max_output_bytes
            ):
                raise OSError
            opened = self.storage.open_render(
                job.artifact_storage_key, job.project_id, job.id, job.artifact_byte_size
            )
            before = opened.stat_result
            if stat.S_IMODE(before.st_mode) != 0o400:
                raise OSError
            if verify_checksum:
                digest, count = hashlib.sha256(), 0
                while chunk := opened.file.read(CHUNK_BYTES):
                    count += len(chunk)
                    if count > job.artifact_byte_size:
                        raise OSError
                    digest.update(chunk)
                after = os.fstat(opened.file.fileno())
                if (
                    count != job.artifact_byte_size
                    or digest.hexdigest() != job.artifact_sha256
                    or (before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_mode)
                    != (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_mode)
                ):
                    raise OSError
                opened.file.seek(0)
            return opened
        except (AppError, OSError, ValueError):
            if opened is not None:
                opened.file.close()
            raise AppError(
                503, "render_artifact_unavailable", "O arquivo exportado está indisponível."
            ) from None

    def _response(self, job, revision, *, artifact_available=False, unavailable=False):
        plan = self._plan(revision)
        base = f"/api/v1/projects/{job.project_id}/renders/{job.id}"
        completed = job.status == "completed"
        code = "render_artifact_unavailable" if unavailable else job.error_code
        if code is not None and re.fullmatch(r"render_[a-z0-9_]{1,57}", code) is None:
            code = "render_processing_failed"
        duration = plan.trim.end_ms - plan.trim.start_ms
        percent = None
        if (
            job.status == "running"
            and job.stage == "encoding"
            and job.progress_encoded_ms is not None
        ):
            percent = min(99, max(0, job.progress_encoded_ms * 100 // duration))
        if completed:
            percent = 100
        return RenderResponse(
            id=job.id,
            project_id=job.project_id,
            revision_id=revision.id,
            revision_number=revision.revision_number,
            profile=job.profile,
            preset=plan.canvas.preset,
            render_engine_version=job.render_engine_version,
            status=job.status,
            stage=job.stage,
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            progress_encoded_ms=job.progress_encoded_ms,
            progress_percent=percent,
            cancel_requested_at=job.cancel_requested_at,
            created_at=job.created_at,
            updated_at=job.updated_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            error_code=code,
            artifact_available=artifact_available,
            checksum_sha256=job.artifact_sha256 if completed else None,
            byte_size=job.artifact_byte_size if completed else None,
            duration_ms=job.artifact_duration_ms if completed else None,
            width=job.artifact_width if completed else None,
            height=job.artifact_height if completed else None,
            fps_num=job.artifact_fps_num if completed else None,
            fps_den=job.artifact_fps_den if completed else None,
            video_codec=job.artifact_video_codec if completed else None,
            audio_codec=job.artifact_audio_codec if completed else None,
            status_url=base,
            file_url=f"{base}/file" if artifact_available else None,
        )

    def create(self, project_id: UUID, data: RenderCreate) -> tuple[RenderResponse, bool]:
        ProjectService(self.session).get(project_id, for_update=True)
        revision = self._revision(project_id, data.edit_spec_revision_id)
        fingerprint = hashlib.sha256(
            f"{project_id}:{revision.id}:{data.profile}:{RENDER_ENGINE_VERSION}".encode("ascii")
        ).hexdigest()
        key = str(data.idempotency_key) if data.idempotency_key is not None else None
        if key is not None:
            previous = self.repository.get_by_key(project_id, key)
            if previous is not None:
                if (
                    previous.edit_spec_revision_id != revision.id
                    or previous.profile != data.profile
                ):
                    raise AppError(
                        409, "idempotency_conflict", "A chave já foi usada para outra exportação."
                    )
                if previous.render_engine_version != RENDER_ENGINE_VERSION:
                    raise AppError(
                        409,
                        "render_engine_conflict",
                        "A chave pertence a outra versão do motor. Solicite uma nova exportação.",
                    )
                ready = previous.status == "completed"
                if ready:
                    self._artifact(previous).file.close()
                result = self._response(previous, revision, artifact_available=ready)
                self.session.commit()
                return result, previous.status not in {"queued", "running"}
        else:
            for previous in self.repository.equivalents(project_id, fingerprint):
                ready = previous.status == "completed"
                if ready:
                    try:
                        self._artifact(previous).file.close()
                    except AppError:
                        # Preserve completed history and all existing bytes.
                        continue
                result = self._response(previous, revision, artifact_available=ready)
                self.session.commit()
                return result, ready
        if self.repository.count_active(project_id) >= self.settings.render_max_active_per_project:
            raise AppError(
                409,
                "render_active_limit",
                "Este projeto já possui uma exportação na fila ou em execução.",
            )
        self._validate_inputs(project_id, revision)
        self.repository.lock_global_admission()
        if self.repository.count_active_total() >= self.settings.render_max_active_total:
            raise AppError(
                409,
                "render_queue_full",
                "A fila de exportações está cheia. Aguarde ou cancele um trabalho pendente.",
            )
        job = RenderJob(
            id=uuid4(),
            project_id=project_id,
            edit_spec_revision_id=revision.id,
            profile=data.profile,
            render_engine_version=RENDER_ENGINE_VERSION,
            idempotency_key=key,
            request_fingerprint=fingerprint,
            max_attempts=self.settings.render_max_attempts,
        )
        self.repository.add(job)
        self.session.flush()
        result = self._response(job, revision)
        self.session.commit()
        return result, False

    def get(self, project_id: UUID, render_id: UUID) -> RenderResponse:
        job, revision = self._job(project_id, render_id)
        ready, unavailable = False, False
        if job.status == "completed":
            try:
                self._artifact(job).file.close()
                ready = True
            except AppError:
                unavailable = True
        return self._response(job, revision, artifact_available=ready, unavailable=unavailable)

    def list(
        self, project_id: UUID, limit: int, offset: int, status: str | None = None
    ) -> RenderPage:
        ProjectService(self.session).get(project_id)
        jobs, total = self.repository.list_for_project(project_id, limit, offset, status)
        items = []
        for job in jobs:
            revision = self._revision(project_id, job.edit_spec_revision_id)
            ready, unavailable = False, False
            if job.status == "completed":
                try:
                    # History verifies presence/mode/size, not 2 GiB of SHA per row.
                    # Full SHA is mandatory in detail, reuse and download.
                    self._artifact(job, verify_checksum=False).file.close()
                    ready = True
                except AppError:
                    unavailable = True
            items.append(
                self._response(job, revision, artifact_available=ready, unavailable=unavailable)
            )
        return RenderPage(items=items, total=total, limit=limit, offset=offset)

    def cancel(self, project_id: UUID, render_id: UUID) -> tuple[RenderResponse, bool]:
        job, revision = self._job(project_id, render_id, for_update=True)
        if job.status == "canceled":
            result = self._response(job, revision)
            self.session.commit()
            return result, False
        if job.status not in {"queued", "running"}:
            raise AppError(
                409,
                "render_not_cancelable",
                "Esta exportação já foi finalizada e não pode ser cancelada.",
            )
        pending = self.repository.request_cancel(job)
        result = self._response(job, revision)
        self.session.commit()
        return result, pending

    def open_file(self, project_id: UUID, render_id: UUID) -> tuple[OpenedOriginal, str, str]:
        job, revision = self._job(project_id, render_id)
        if job.status != "completed":
            raise AppError(
                409, "render_not_ready", "A exportação ainda não está disponível para download."
            )
        preset = self._plan(revision).canvas.preset.replace(":", "x")
        filename = f"edivo-r{revision.revision_number}-{preset}-{job.profile}.mp4"
        return self._artifact(job), job.artifact_sha256, filename
