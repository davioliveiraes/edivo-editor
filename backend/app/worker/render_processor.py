"""Render a saved revision with the R04 compositor and publish only validated MP4."""

import logging
import os
import stat
import tempfile
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from threading import Event
from time import monotonic

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.core.errors import AppError
from app.integrations.ffmpeg.preview import FINAL_FPS, FINAL_PROFILE_SIZES
from app.repositories.preview_jobs import database_now
from app.repositories.projects import ProjectRepository
from app.repositories.render_jobs import (
    RENDER_ENGINE_VERSION,
    RenderClaim,
    RenderJobRepository,
)
from app.storage.local import LocalStorage, StagedFile
from app.worker.preview_worker import SessionFactory
from app.worker.processor import PreviewInputs, PreviewProcessor, hash_pinned, load_revision_inputs
from app.worker.render_worker import RenderLeaseController, render_log_fields
from app.worker.supervisor import (
    LeaseLost,
    PreviewProcessError,
    ProcessPolicy,
    ProcessRunner,
    ProcessStopped,
    RenderCanceled,
)

logger = logging.getLogger("edivo.worker")


class RenderProcessor:
    def __init__(
        self,
        settings: Settings,
        sessions: SessionFactory,
        stopped: Event,
        *,
        renderer: Callable | None = None,
    ) -> None:
        if renderer is None:
            from app.integrations.ffmpeg.preview import render_final

            renderer = render_final
        self.settings, self.sessions, self.stopped = settings, sessions, stopped
        self.renderer = renderer

    def load_inputs(self, claim: RenderClaim) -> PreviewInputs:
        with self.sessions() as session:
            if ProjectRepository(session).get_active(claim.project_id) is None:
                raise PreviewProcessError("render_project_unavailable")
            job = RenderJobRepository(session).get_for_project(claim.project_id, claim.id)
            if (
                job is None
                or job.edit_spec_revision_id != claim.edit_spec_revision_id
                or job.profile != claim.profile
                or job.render_engine_version != claim.render_engine_version
                or claim.render_engine_version != RENDER_ENGINE_VERSION
                or claim.profile not in {"standard", "high"}
            ):
                raise PreviewProcessError("render_plan_invalid")
            inputs = load_revision_inputs(session, claim.project_id, claim.edit_spec_revision_id)
            duration = inputs.plan.trim.end_ms - inputs.plan.trim.start_ms
            if not 1 <= duration <= self.settings.media_max_duration_seconds * 1000:
                raise PreviewProcessError("render_plan_invalid")
            return inputs

    def publication_control(self, repository, claim, runner) -> None:
        # No external heartbeat while holding Project/Job: it would block on our
        # own transaction. The current connection observes a fresh wall clock.
        if self.stopped.is_set():
            raise ProcessStopped()
        if monotonic() >= runner.deadline:
            raise PreviewProcessError("render_timeout")
        state = repository.control_state(claim)
        if state == "cancel_requested":
            raise RenderCanceled()
        if state != "owned":
            raise LeaseLost()

    def publish(self, claim, staged, metadata, runner, control) -> None:
        runner.pulse()
        control.set_stage("publishing")
        storage = LocalStorage(self.settings)
        with self.sessions() as session, session.begin():
            if ProjectRepository(session).get_active(claim.project_id, for_update=True) is None:
                raise PreviewProcessError("render_project_unavailable")
            repository = RenderJobRepository(session)
            job = repository.lock_owned_for_publish(claim)
            self.publication_control(repository, claim, runner)
            if job is None:
                raise LeaseLost()
            if (
                job.edit_spec_revision_id != claim.edit_spec_revision_id
                or job.profile != claim.profile
                or job.render_engine_version != claim.render_engine_version
            ):
                raise PreviewProcessError("render_plan_invalid")
            key = storage.promote_render(staged, claim.project_id, claim.id)
            self.publication_control(repository, claim, runner)
            job.status, job.stage = "completed", "completed"
            job.artifact_storage_key = key
            job.artifact_sha256 = staged.checksum_sha256
            job.artifact_byte_size = staged.byte_size
            job.artifact_duration_ms = metadata["duration_ms"]
            job.artifact_width, job.artifact_height = metadata["width"], metadata["height"]
            job.artifact_video_codec, job.artifact_audio_codec = (
                metadata["video_codec"],
                metadata["audio_codec"],
            )
            job.artifact_fps_num, job.artifact_fps_den = metadata["fps_num"], metadata["fps_den"]
            job.finished_at, job.updated_at = database_now(), database_now()
            job.claim_token, job.lease_expires_at, job.error_code = None, None, None
            # Never remove a canonical artifact after an uncertain COMMIT reply.
            # A successor does not adopt/overwrite it; read-only audit reconciles.
        logger.info(
            "render_job_completed", extra={**render_log_fields(claim), "stage": "completed"}
        )

    @staticmethod
    def output_metadata(artifact, inputs: PreviewInputs) -> dict:
        """Guard the publication boundary in addition to the compositor's full QA."""
        metadata = artifact.metadata
        expected_size = FINAL_PROFILE_SIZES[inputs.plan.canvas.preset]
        duration = inputs.plan.trim.end_ms - inputs.plan.trim.start_ms
        expects_audio = inputs.source_has_audio or inputs.background is not None
        if (
            not isinstance(metadata, dict)
            or type(metadata.get("duration_ms")) is not int
            or metadata["duration_ms"] <= 0
            or abs(metadata["duration_ms"] - duration) > 1000 / FINAL_FPS + 1
            or (metadata.get("width"), metadata.get("height")) != expected_size
            or (metadata.get("fps_num"), metadata.get("fps_den")) != (FINAL_FPS, 1)
            or metadata.get("video_codec") != "h264"
            or metadata.get("pixel_format") != "yuv420p"
            or metadata.get("sample_aspect_ratio") != "1:1"
            or metadata.get("has_audio") is not expects_audio
            or metadata.get("audio_codec") != ("aac" if expects_audio else None)
        ):
            raise PreviewProcessError("render_validation_failed")
        return {
            key: metadata[key]
            for key in (
                "duration_ms",
                "width",
                "height",
                "fps_num",
                "fps_den",
                "video_codec",
                "audio_codec",
            )
        }

    def __call__(self, claim: RenderClaim, control: RenderLeaseController) -> None:
        runner = ProcessRunner(
            self.settings,
            self.stopped,
            control.heartbeat,
            policy=ProcessPolicy.for_render(self.settings),
            control=control.control,
        )
        try:
            runner.pulse()
            inputs = self.load_inputs(claim)
            storage = LocalStorage(self.settings)
            directory = storage.prepare_temp()
            storage.ensure_space(self.settings.render_max_output_bytes)
            with (
                tempfile.TemporaryDirectory(
                    prefix=f"render-{claim.id}-{claim.claim_token}-", dir=directory
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
                PreviewProcessor.verify_input(source, inputs.source, runner)
                background_fd = None
                if inputs.background is not None:
                    background = storage.open_audio(
                        inputs.background.storage_key,
                        claim.project_id,
                        inputs.background.id,
                        inputs.background.byte_size,
                    )
                    handles.callback(background.file.close)
                    PreviewProcessor.verify_input(background, inputs.background, runner)
                    background_fd = background.file.fileno()
                artifact = self.renderer(
                    self.settings,
                    inputs.plan,
                    source_fd=source.file.fileno(),
                    source_video_stream_index=inputs.video_stream_index,
                    source_has_audio=inputs.source_has_audio,
                    background_fd=background_fd,
                    workspace=workspace,
                    runner=runner,
                    set_stage=control.set_stage,
                    profile=claim.profile,
                    set_progress=control.set_progress,
                )
                if artifact.path != workspace / "final.part":
                    raise PreviewProcessError("render_validation_failed")
                metadata = self.output_metadata(artifact, inputs)
                descriptor = os.open(
                    artifact.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
                )
                with os.fdopen(descriptor, "rb") as output:
                    before = os.fstat(output.fileno())
                    if (
                        not stat.S_ISREG(before.st_mode)
                        or not 0 < before.st_size <= self.settings.render_max_output_bytes
                        or before.st_size != artifact.byte_size
                        or hash_pinned(output, before.st_size, runner) != artifact.checksum_sha256
                    ):
                        raise PreviewProcessError("render_validation_failed")
                    after = os.fstat(output.fileno())
                    if (before.st_size, before.st_mode, before.st_mtime_ns, before.st_ctime_ns) != (
                        after.st_size,
                        after.st_mode,
                        after.st_mtime_ns,
                        after.st_ctime_ns,
                    ):
                        raise PreviewProcessError("render_validation_failed")
                staged = StagedFile(artifact.path, artifact.byte_size, artifact.checksum_sha256)
                self.publish(claim, staged, metadata, runner, control)
        except AppError as error:
            code = error.code if error.code.startswith("render_") else "render_storage_unavailable"
            raise PreviewProcessError(code) from None
        except ValidationError:
            raise PreviewProcessError("render_plan_invalid") from None
        except SQLAlchemyError:
            raise PreviewProcessError("render_database_unavailable") from None
        except OSError:
            raise PreviewProcessError("render_storage_unavailable") from None
        except PreviewProcessError as error:
            if type(error) is PreviewProcessError and error.code.startswith("preview_"):
                raise PreviewProcessError("render_" + error.code.removeprefix("preview_")) from None
            raise
