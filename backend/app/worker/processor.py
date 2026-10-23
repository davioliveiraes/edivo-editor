"""Compose one immutable revision and publish only its verified private preview."""

import hashlib
import logging
import os
import stat
import tempfile
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from time import monotonic
from typing import BinaryIO
from uuid import UUID

from pydantic import TypeAdapter, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.models.audio_asset import AudioAsset
from app.models.edit_spec_revision import EditSpecRevision
from app.models.media_asset import MediaAsset
from app.repositories.preview_jobs import PreviewClaim, PreviewJobRepository, database_now
from app.repositories.projects import ProjectRepository
from app.schemas.edit_spec import EditSpec, EditSpecV2
from app.storage.local import LocalStorage, StagedFile
from app.worker.preview_worker import LeaseController, SessionFactory, claim_log_fields
from app.worker.supervisor import LeaseLost, PreviewProcessError, ProcessRunner, ProcessStopped

PLAN_ADAPTER = TypeAdapter(EditSpec)
logger = logging.getLogger("edivo.worker")


@dataclass(frozen=True)
class InputAsset:
    id: UUID
    storage_key: str
    byte_size: int
    checksum_sha256: str


@dataclass(frozen=True)
class PreviewInputs:
    plan: EditSpec
    source: InputAsset
    background: InputAsset | None
    video_stream_index: int
    source_has_audio: bool


def load_revision_inputs(session: Session, project_id: UUID, revision_id: UUID) -> PreviewInputs:
    """Authorize the immutable composition inputs shared by previews and exports.

    Return detached values only: filesystem reads and encoding must take place
    after this short read transaction closes. The caller adds job-specific rules.
    """
    if ProjectRepository(session).get_active(project_id) is None:
        raise PreviewProcessError("preview_project_unavailable")
    revision = session.get(EditSpecRevision, revision_id)
    if revision is None or revision.project_id != project_id:
        raise PreviewProcessError("preview_plan_invalid")
    plan = PLAN_ADAPTER.validate_python(revision.spec_json)
    if (
        plan.schema_version != revision.schema_version
        or plan.source_media_id != revision.source_media_id
    ):
        raise PreviewProcessError("preview_plan_invalid")
    source = session.get(MediaAsset, plan.source_media_id)
    if (
        source is None
        or source.project_id != project_id
        or source.kind != "source_video"
        or source.mime_detected != "video/mp4"
        or plan.trim.end_ms > source.duration_ms
        or plan.trim.end_ms - plan.trim.start_ms < min(100, source.duration_ms)
    ):
        raise PreviewProcessError("preview_plan_invalid")
    stream_index = source.probe_metadata.get("video_stream_index")
    if type(stream_index) is not int or not 0 <= stream_index <= 127:
        raise PreviewProcessError("preview_source_unavailable")
    background = None
    if isinstance(plan, EditSpecV2) and plan.audio.background_audio_id is not None:
        audio = session.get(AudioAsset, plan.audio.background_audio_id)
        if (
            audio is None
            or audio.project_id != project_id
            or audio.mime_detected != "audio/mpeg"
            or audio.codec != "mp3"
        ):
            raise PreviewProcessError("preview_audio_unavailable")
        background = InputAsset(audio.id, audio.storage_key, audio.byte_size, audio.checksum_sha256)
    return PreviewInputs(
        plan,
        InputAsset(source.id, source.storage_key, source.byte_size, source.checksum_sha256),
        background,
        stream_index,
        source.has_audio,
    )


def hash_pinned(file: BinaryIO, expected_bytes: int, runner: ProcessRunner) -> str:
    digest, actual_bytes = hashlib.sha256(), 0
    file.seek(0)
    os.lseek(file.fileno(), 0, os.SEEK_SET)
    while chunk := file.read(1024 * 1024):
        runner.pulse()
        actual_bytes += len(chunk)
        if actual_bytes > expected_bytes:
            raise PreviewProcessError("preview_source_integrity_failed")
        digest.update(chunk)
    runner.pulse()
    file.seek(0)
    os.lseek(file.fileno(), 0, os.SEEK_SET)
    if actual_bytes != expected_bytes:
        raise PreviewProcessError("preview_source_integrity_failed")
    return digest.hexdigest()


class PreviewProcessor:
    def __init__(
        self,
        settings: Settings,
        sessions: SessionFactory,
        stopped: Event,
        *,
        renderer: Callable | None = None,
    ) -> None:
        if renderer is None:
            from app.integrations.ffmpeg.preview import render_preview

            renderer = render_preview
        self.settings, self.sessions, self.stopped = settings, sessions, stopped
        self.renderer = renderer

    def load_inputs(self, claim: PreviewClaim) -> PreviewInputs:
        with self.sessions() as session:
            if ProjectRepository(session).get_active(claim.project_id) is None:
                raise PreviewProcessError("preview_project_unavailable")
            job = PreviewJobRepository(session).get_for_project(claim.project_id, claim.id)
            revision = session.get(EditSpecRevision, claim.edit_spec_revision_id)
            if (
                job is None
                or job.edit_spec_revision_id != claim.edit_spec_revision_id
                or job.preview_start_ms != claim.preview_start_ms
                or job.duration_ms != claim.duration_ms
                or job.profile_version != claim.profile_version
                or revision is None
                or revision.project_id != claim.project_id
                or claim.profile_version != 1
            ):
                raise PreviewProcessError("preview_plan_invalid")
            inputs = load_revision_inputs(session, claim.project_id, claim.edit_spec_revision_id)
            if (
                not 1 <= claim.duration_ms <= 8000
                or claim.preview_start_ms < inputs.plan.trim.start_ms
                or claim.preview_start_ms + claim.duration_ms > inputs.plan.trim.end_ms
            ):
                raise PreviewProcessError("preview_plan_invalid")
            return inputs

    @staticmethod
    def verify_input(opened, asset: InputAsset, runner: ProcessRunner) -> None:
        before = os.fstat(opened.file.fileno())
        if stat.S_IMODE(before.st_mode) != 0o400:
            raise PreviewProcessError("preview_source_integrity_failed")
        if hash_pinned(opened.file, asset.byte_size, runner) != asset.checksum_sha256:
            raise PreviewProcessError("preview_source_integrity_failed")
        after = os.fstat(opened.file.fileno())
        if (before.st_size, before.st_mode, before.st_mtime_ns) != (
            after.st_size,
            after.st_mode,
            after.st_mtime_ns,
        ):
            raise PreviewProcessError("preview_source_integrity_failed")

    def publication_control(self, repository, claim, runner) -> None:
        # Do not call heartbeat through another connection while holding this
        # job's row lock: it would wait for our own transaction. Use this session.
        if self.stopped.is_set():
            raise ProcessStopped()
        if monotonic() >= runner.deadline:
            raise PreviewProcessError("preview_timeout")
        if not repository.is_owned(claim):
            raise LeaseLost()

    def publish(self, claim, staged, runner, control) -> None:
        runner.pulse()
        control.set_stage("publishing")
        storage = LocalStorage(self.settings)
        with self.sessions() as session, session.begin():
            if ProjectRepository(session).get_active(claim.project_id, for_update=True) is None:
                raise PreviewProcessError("preview_project_unavailable")
            repository = PreviewJobRepository(session)
            job = repository.lock_owned_for_publish(claim)
            if job is None:
                raise LeaseLost()
            self.publication_control(repository, claim, runner)
            key = storage.promote_preview(staged, claim.project_id, claim.id)
            self.publication_control(repository, claim, runner)
            job.status, job.stage = "completed", "completed"
            job.artifact_storage_key = key
            job.artifact_sha256 = staged.checksum_sha256
            job.artifact_byte_size = staged.byte_size
            job.finished_at = database_now()
            job.claim_token, job.lease_expires_at, job.error_code = None, None, None
            # A lost commit reply never authorizes removing the canonical file.
            # This method and its callers have no canonical cleanup path.
        logger.info(
            "preview_job_completed", extra={**claim_log_fields(claim), "stage": "completed"}
        )

    def __call__(self, claim: PreviewClaim, control: LeaseController) -> None:
        runner = ProcessRunner(self.settings, self.stopped, control.heartbeat)
        try:
            runner.pulse()
            inputs = self.load_inputs(claim)
            storage = LocalStorage(self.settings)
            directory = storage.prepare_temp()
            storage.ensure_space(self.settings.preview_max_bytes)
            with (
                tempfile.TemporaryDirectory(
                    prefix=f"preview-{claim.id}-{claim.claim_token}-", dir=directory
                ) as temporary,
                ExitStack() as handles,
            ):
                workspace = Path(temporary)
                source = storage.open_original(
                    inputs.source.storage_key,
                    claim.project_id,
                    inputs.source.id,
                    inputs.source.byte_size,
                )
                handles.callback(source.file.close)
                self.verify_input(source, inputs.source, runner)
                background_fd = None
                if inputs.background is not None:
                    background = storage.open_audio(
                        inputs.background.storage_key,
                        claim.project_id,
                        inputs.background.id,
                        inputs.background.byte_size,
                    )
                    handles.callback(background.file.close)
                    self.verify_input(background, inputs.background, runner)
                    background_fd = background.file.fileno()
                artifact = self.renderer(
                    self.settings,
                    inputs.plan,
                    source_fd=source.file.fileno(),
                    source_video_stream_index=inputs.video_stream_index,
                    source_has_audio=inputs.source_has_audio,
                    background_fd=background_fd,
                    preview_start_ms=claim.preview_start_ms,
                    duration_ms=claim.duration_ms,
                    workspace=workspace,
                    runner=runner,
                    set_stage=control.set_stage,
                )
                if artifact.path != workspace / "preview.part":
                    raise PreviewProcessError("preview_validation_failed")
                descriptor = os.open(artifact.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
                with os.fdopen(descriptor, "rb") as output:
                    metadata = os.fstat(output.fileno())
                    if (
                        not stat.S_ISREG(metadata.st_mode)
                        or not 0 < metadata.st_size <= self.settings.preview_max_bytes
                        or metadata.st_size != artifact.byte_size
                        or hash_pinned(output, metadata.st_size, runner) != artifact.checksum_sha256
                    ):
                        raise PreviewProcessError("preview_validation_failed")
                staged = StagedFile(artifact.path, artifact.byte_size, artifact.checksum_sha256)
                self.publish(claim, staged, runner, control)
        except AppError as error:
            code = (
                error.code if error.code.startswith("preview_") else "preview_storage_unavailable"
            )
            raise PreviewProcessError(code) from None
        except ValidationError:
            raise PreviewProcessError("preview_plan_invalid") from None
        except SQLAlchemyError:
            raise PreviewProcessError("preview_database_unavailable") from None
        except OSError:
            raise PreviewProcessError("preview_storage_unavailable") from None
