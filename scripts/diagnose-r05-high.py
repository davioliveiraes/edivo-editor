"""One diagnostic composition of an immutable revision, never a queued job.

Run via API Python stdin. Captures bounded child stderr privately while using
unchanged production ProcessRunner and resource policy. No publication or DB
mutation; only this invocation's private workspace is created/removed.
"""

import json
import os
import subprocess
import tempfile
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
from threading import Event, Thread
from time import monotonic
from unittest.mock import patch
from uuid import UUID

from app.core.config import Settings
from app.db.session import Database
from app.integrations.ffmpeg.preview import render_final
from app.models.render_job import RenderJob
from app.storage.local import LocalStorage
from app.worker.processor import PreviewProcessor, load_revision_inputs
from app.worker.supervisor import PreviewProcessError, ProcessPolicy, ProcessRunner

PROJECT_ID = UUID("3f4bc7e0-3b4e-4db6-a812-18b581a398e3")
FAILED_JOB_ID = UUID("79a1e1e9-2692-4337-b6ad-45274f7d8231")


class DiagnosticRunner(ProcessRunner):
    def __init__(self, settings):
        super().__init__(settings, Event(), policy=ProcessPolicy.for_render(settings))
        self.commands = []

    def run(self, argv, **kwargs):
        # Tee only stderr reads into a bounded diagnostic. The real ProcessRunner
        # still starts/selects/supervises/limits/waits exactly the same child.
        record = {"executable": Path(argv[0]).name, "stderr": "", "returncode": None}
        capture = bytearray()
        descriptor = None
        sampled = Event()
        observer = None
        arenas = os.environ.get("EDIVO_QA_MALLOC_ARENA_MAX")
        assert arenas in (None, "2")
        real_popen, real_read = subprocess.Popen, os.read

        def popen(*args, **options):
            nonlocal descriptor, observer
            if arenas is not None:
                options["env"] = {**options["env"], "MALLOC_ARENA_MAX": arenas}
            record["malloc_arena_max"] = arenas
            process = real_popen(*args, **options)
            descriptor = process.stderr.fileno()

            def sample():
                peaks = {}
                while not sampled.wait(0.02):
                    try:
                        lines = (
                            Path(f"/proc/{process.pid}/status").read_text().splitlines()
                        )
                    except OSError:
                        break
                    for line in lines:
                        name, _, value = line.partition(":")
                        if name in {"VmPeak", "VmSize", "VmRSS", "VmHWM", "Threads"}:
                            number = int(value.strip().split()[0])
                            peaks[name] = max(peaks.get(name, 0), number)
                record["observed_proc_maxima_kib_except_threads"] = peaks

            observer = Thread(target=sample, daemon=True)
            observer.start()
            return process

        def read(fd, count):
            data = real_read(fd, count)
            if fd == descriptor and len(capture) < self.policy.output_bytes:
                capture.extend(data[: self.policy.output_bytes - len(capture)])
            return data

        try:
            with (
                patch("app.worker.supervisor.subprocess.Popen", popen),
                patch("app.worker.supervisor.os.read", read),
            ):
                result = super().run(argv, **kwargs)
            record["returncode"] = result.returncode
            return result
        finally:
            sampled.set()
            if observer is not None:
                observer.join(timeout=2)
            record["stderr"] = capture.decode("utf-8", errors="replace")
            record["captured_stderr_bytes"] = len(capture)
            self.commands.append(record)


def main():
    started = monotonic()
    settings, report = (
        Settings(),
        {
            "diagnostic_only": True,
            "database_mutated": False,
            "publication_performed": False,
            "project_id": str(PROJECT_ID),
            "failed_job_id": str(FAILED_JOB_ID),
        },
    )
    database, storage, runner = Database(settings), LocalStorage(settings), None
    try:
        with database.sessions() as session:
            job = session.get(RenderJob, FAILED_JOB_ID)
            assert job.project_id == PROJECT_ID and job.status == "failed"
            before = (job.status, job.updated_at.isoformat(), job.attempts)
            inputs = load_revision_inputs(
                session, PROJECT_ID, job.edit_spec_revision_id
            )
            profile = job.profile
            report.update(
                revision_id=str(job.edit_spec_revision_id),
                profile=profile,
                preset=inputs.plan.canvas.preset,
                trim_ms=[inputs.plan.trim.start_ms, inputs.plan.trim.end_ms],
            )
        runner = DiagnosticRunner(settings)
        report["process_policy"] = asdict(runner.policy)
        stages, progress = [], []
        with (
            tempfile.TemporaryDirectory(
                prefix="diagnostic-r05-high-", dir=storage.prepare_temp()
            ) as temp,
            ExitStack() as handles,
        ):
            original = storage.open_original(
                inputs.source.storage_key,
                PROJECT_ID,
                inputs.source.id,
                inputs.source.byte_size,
            )
            handles.callback(original.file.close)
            PreviewProcessor.verify_input(original, inputs.source, runner)
            background = None
            if inputs.background:
                background = storage.open_audio(
                    inputs.background.storage_key,
                    PROJECT_ID,
                    inputs.background.id,
                    inputs.background.byte_size,
                )
                handles.callback(background.file.close)
                PreviewProcessor.verify_input(background, inputs.background, runner)
            try:
                artifact = render_final(
                    settings,
                    inputs.plan,
                    source_fd=original.file.fileno(),
                    source_video_stream_index=inputs.video_stream_index,
                    source_has_audio=inputs.source_has_audio,
                    background_fd=background.file.fileno() if background else None,
                    workspace=Path(temp),
                    runner=runner,
                    set_stage=stages.append,
                    profile=profile,
                    set_progress=progress.append,
                )
                report.update(
                    status="passed",
                    artifact_metadata=artifact.metadata,
                    checksum_sha256=artifact.checksum_sha256,
                    byte_size=artifact.byte_size,
                )
            except PreviewProcessError as error:
                report.update(status="failed", error_code=error.code)
            report.update(stages=stages, encoded_ms=progress)
            report["inputs_after"] = {}
            for opened, asset in (
                (original, inputs.source),
                (background, inputs.background),
            ):
                if opened is not None:
                    PreviewProcessor.verify_input(opened, asset, runner)
                    report["inputs_after"][str(asset.id)] = {
                        "checksum_sha256": asset.checksum_sha256,
                        "mode": "0400",
                    }
        with database.sessions() as session:
            current = session.get(RenderJob, FAILED_JOB_ID)
            report["failed_job_unchanged"] = before == (
                current.status,
                current.updated_at.isoformat(),
                current.attempts,
            )
    finally:
        database.close()
        report["elapsed_seconds"] = round(monotonic() - started, 3)
        report["commands"] = runner.commands if runner else []
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
