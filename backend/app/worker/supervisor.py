"""Bounded child-process supervision with lease checks and group termination."""

import json
import os
import re
import selectors
import signal
import subprocess
import sys
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Event
from time import monotonic, sleep

from app.core.config import Settings


class PreviewProcessError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = (
            code if re.fullmatch(r"preview_[a-z0-9_]{1,56}", code) else "preview_processing_failed"
        )
        super().__init__(self.code)


class ProcessStopped(PreviewProcessError):
    def __init__(self) -> None:
        super().__init__("preview_worker_interrupted")


class LeaseLost(PreviewProcessError):
    def __init__(self) -> None:
        super().__init__("preview_lease_lost")


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: bytes


@dataclass(frozen=True)
class ProcessLimits:
    memory_bytes: int
    cpu_seconds: int
    file_size_bytes: int
    open_files: int = 128


def terminate_group(process: subprocess.Popen, grace_seconds: float = 1.0) -> None:
    """Signal the session even if the leader has exited while a child holds a pipe.

    Tini (already enabled in Compose) reaps adopted descendants. The immediate
    subprocess is always waited by this supervisor.
    """
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    deadline = monotonic() + grace_seconds
    while monotonic() < deadline:
        process.poll()
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        sleep(0.02)
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=2)
    for pipe in (process.stdout, process.stderr):
        if pipe is not None:
            pipe.close()


class ProcessRunner:
    def __init__(
        self,
        settings: Settings,
        stopped: Event,
        heartbeat: Callable[[], bool] | None = None,
    ) -> None:
        self.settings = settings
        self.stopped = stopped
        self.heartbeat = heartbeat
        self.deadline = monotonic() + settings.preview_ffmpeg_timeout_seconds
        self.next_heartbeat = 0.0

    def pulse(self) -> None:
        """Check the whole-job budget during hashing/preparation as well as encoding."""
        if self.stopped.is_set():
            raise ProcessStopped()
        now = monotonic()
        if now >= self.deadline:
            raise PreviewProcessError("preview_timeout")
        if now >= self.next_heartbeat:
            if self.heartbeat is not None and not self.heartbeat():
                raise LeaseLost()
            self.next_heartbeat = monotonic() + self.settings.preview_heartbeat_seconds

    check = pulse

    def run(
        self,
        argv: list[str],
        *,
        pass_fds: tuple[int, ...] = (),
        timeout: float | None = None,
        output_limit: int | None = None,
        cwd: Path | None = None,
    ) -> ProcessResult:
        self.pulse()
        timeout_seconds = (
            timeout if timeout is not None else self.settings.preview_ffmpeg_timeout_seconds
        )
        output_bytes = (
            output_limit if output_limit is not None else self.settings.preview_process_output_bytes
        )
        if not argv or timeout_seconds <= 0 or output_bytes <= 0:
            raise PreviewProcessError("preview_process_configuration_invalid")
        limits = ProcessLimits(
            self.settings.preview_memory_bytes,
            self.settings.preview_cpu_seconds,
            self.settings.preview_max_bytes,
        )
        launcher = Path(__file__).with_name("resource_exec.py")
        try:
            process = subprocess.Popen(
                [sys.executable, str(launcher), json.dumps(asdict(limits)), *argv],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                pass_fds=pass_fds,
                cwd=cwd,
                env={
                    "PATH": os.environ.get("PATH", os.defpath),
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                umask=0o077,
            )
        except OSError:
            raise PreviewProcessError("preview_process_unavailable") from None
        stdout = bytearray()
        total_bytes = 0
        deadline = min(self.deadline, monotonic() + timeout_seconds)
        try:
            with selectors.DefaultSelector() as selector:
                assert process.stdout is not None and process.stderr is not None
                selector.register(process.stdout, selectors.EVENT_READ, "stdout")
                selector.register(process.stderr, selectors.EVENT_READ, "stderr")
                while selector.get_map():
                    self.pulse()
                    now = monotonic()
                    if now >= deadline:
                        raise PreviewProcessError("preview_timeout")
                    for key, _ in selector.select(min(deadline - now, 0.1)):
                        chunk = os.read(key.fileobj.fileno(), min(65536, output_bytes + 1))
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        total_bytes += len(chunk)
                        if total_bytes > output_bytes:
                            raise PreviewProcessError("preview_process_output_exceeded")
                        if key.data == "stdout":
                            stdout.extend(chunk)
                # A child may close its pipes before finishing; still enforce control.
                while process.poll() is None:
                    self.pulse()
                    if monotonic() >= deadline:
                        raise PreviewProcessError("preview_timeout")
                    if self.stopped.wait(0.05):
                        raise ProcessStopped()
                self.pulse()
                return ProcessResult(process.wait(), bytes(stdout))
        finally:
            terminate_group(process)
