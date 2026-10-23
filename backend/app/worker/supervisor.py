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
            code
            if re.fullmatch(r"(?:preview|render)_[a-z0-9_]{1,56}", code)
            else "preview_processing_failed"
        )
        super().__init__(self.code)


class ProcessStopped(PreviewProcessError):
    def __init__(self) -> None:
        super().__init__("preview_worker_interrupted")


class LeaseLost(PreviewProcessError):
    def __init__(self) -> None:
        super().__init__("preview_lease_lost")


class RenderCanceled(PreviewProcessError):
    """Durable cancellation observed by the owner; its child must stop first."""

    def __init__(self) -> None:
        super().__init__("render_canceled")


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


@dataclass(frozen=True)
class ProcessPolicy:
    timeout_seconds: float
    limits: ProcessLimits
    output_bytes: int
    heartbeat_seconds: float
    control_seconds: float
    terminate_grace_seconds: float
    error_prefix: str = "preview"

    @classmethod
    def for_preview(cls, settings: Settings) -> "ProcessPolicy":
        return cls(
            settings.preview_ffmpeg_timeout_seconds,
            ProcessLimits(
                settings.preview_memory_bytes,
                settings.preview_cpu_seconds,
                settings.preview_max_bytes,
            ),
            settings.preview_process_output_bytes,
            settings.preview_heartbeat_seconds,
            0.1,
            1.0,
        )

    @classmethod
    def for_render(cls, settings: Settings) -> "ProcessPolicy":
        return cls(
            settings.render_timeout_seconds,
            ProcessLimits(
                settings.render_memory_bytes,
                settings.render_cpu_seconds,
                settings.render_max_output_bytes,
            ),
            settings.render_process_output_bytes,
            settings.render_heartbeat_seconds,
            settings.render_cancel_poll_seconds,
            settings.render_terminate_grace_seconds,
            "render",
        )


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
        *,
        policy: ProcessPolicy | None = None,
        control: Callable[[], None] | None = None,
    ) -> None:
        self.settings = settings
        self.stopped = stopped
        self.heartbeat = heartbeat
        self.policy = policy or ProcessPolicy.for_preview(settings)
        self.control = control
        self.deadline = monotonic() + self.policy.timeout_seconds
        self.next_heartbeat = 0.0
        self.next_control = 0.0

    def error(self, suffix: str) -> PreviewProcessError:
        return PreviewProcessError(f"{self.policy.error_prefix}_{suffix}")

    def pulse(self) -> None:
        """Check the whole-job budget during hashing/preparation as well as encoding."""
        if self.stopped.is_set():
            raise ProcessStopped()
        now = monotonic()
        if now >= self.deadline:
            raise self.error("timeout")
        if self.control is not None and now >= self.next_control:
            self.control()
            self.next_control = monotonic() + self.policy.control_seconds
        if now >= self.next_heartbeat:
            if self.heartbeat is not None and not self.heartbeat():
                raise LeaseLost()
            self.next_heartbeat = monotonic() + self.policy.heartbeat_seconds

    check = pulse

    def run(
        self,
        argv: list[str],
        *,
        pass_fds: tuple[int, ...] = (),
        timeout: float | None = None,
        output_limit: int | None = None,
        cwd: Path | None = None,
        stdout_consumer: Callable[[bytes], None] | None = None,
        stdout_limit: int | None = None,
    ) -> ProcessResult:
        self.pulse()
        timeout_seconds = timeout if timeout is not None else self.policy.timeout_seconds
        output_bytes = output_limit if output_limit is not None else self.policy.output_bytes
        stdout_budget = stdout_limit if stdout_limit is not None else output_bytes
        if (
            not argv
            or timeout_seconds <= 0
            or output_bytes <= 0
            or stdout_budget <= 0
            or (stdout_limit is not None and stdout_consumer is None)
        ):
            raise self.error("process_configuration_invalid")
        launcher = Path(__file__).with_name("resource_exec.py")
        environment = {
            "PATH": os.environ.get("PATH", os.defpath),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if self.policy.error_prefix == "render":
            # Bound glibc arena reservations for final-resolution x264. The
            # measured high/4:5 audio case otherwise exhausts RLIMIT_AS before
            # reaching its RSS budget. Preview retains its existing environment.
            environment["MALLOC_ARENA_MAX"] = "2"
        try:
            process = subprocess.Popen(
                [sys.executable, str(launcher), json.dumps(asdict(self.policy.limits)), *argv],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                pass_fds=pass_fds,
                cwd=cwd,
                env=environment,
                umask=0o077,
            )
        except OSError:
            raise self.error("process_unavailable") from None
        stdout = bytearray()
        total_bytes = 0
        stdout_bytes = 0
        stderr_bytes = 0
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
                        raise self.error("timeout")
                    for key, _ in selector.select(min(deadline - now, 0.1)):
                        chunk = os.read(key.fileobj.fileno(), min(65536, output_bytes + 1))
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        total_bytes += len(chunk)
                        if stdout_consumer is None:
                            if total_bytes > output_bytes:
                                raise self.error("process_output_exceeded")
                            if key.data == "stdout":
                                stdout.extend(chunk)
                        elif key.data == "stdout":
                            stdout_bytes += len(chunk)
                            if stdout_bytes > stdout_budget:
                                raise self.error("process_output_exceeded")
                            stdout_consumer(chunk)
                        else:
                            stderr_bytes += len(chunk)
                            if stderr_bytes > output_bytes:
                                raise self.error("process_output_exceeded")
                # A child may close its pipes before finishing; still enforce control.
                while process.poll() is None:
                    self.pulse()
                    if monotonic() >= deadline:
                        raise self.error("timeout")
                    if self.stopped.wait(0.05):
                        raise ProcessStopped()
                self.pulse()
                return ProcessResult(process.wait(), bytes(stdout))
        finally:
            terminate_group(process, self.policy.terminate_grace_seconds)
