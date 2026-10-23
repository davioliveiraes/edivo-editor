"""Render supervision against real, isolated POSIX children; no application jobs."""

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from threading import Event
from time import monotonic, sleep

import pytest

from app.worker.supervisor import (
    LeaseLost,
    PreviewProcessError,
    ProcessPolicy,
    ProcessRunner,
    ProcessStopped,
    RenderCanceled,
)


def python(code):
    return [sys.executable, "-c", code]


def render_runner(settings, **overrides):
    configured = settings.model_copy(
        update={
            "preview_ffmpeg_timeout_seconds": 0.001,
            "preview_process_output_bytes": 1,
            "preview_memory_bytes": 64 * 1024**2,
            "preview_cpu_seconds": 1,
            "preview_max_bytes": 1,
            "render_timeout_seconds": 5,
            "render_process_output_bytes": 4096,
            "render_memory_bytes": 256 * 1024**2,
            "render_cpu_seconds": 3,
            "render_max_output_bytes": 8192,
            "render_heartbeat_seconds": 5,
            "render_cancel_poll_seconds": 0.05,
            "render_terminate_grace_seconds": 0.1,
            **overrides,
        }
    )
    return ProcessRunner(configured, Event(), policy=ProcessPolicy.for_render(configured))


def assert_child_stopped(pid):
    deadline = monotonic() + 1
    while monotonic() < deadline:
        try:
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except (FileNotFoundError, ProcessLookupError):
            return
        if state == "Z":
            return  # An adopted zombie awaits host init/Tini, but cannot execute.
        sleep(0.01)
    pytest.fail("the isolated child is still executing")


def test_render_policy_uses_its_own_limits_and_preview_defaults_stay_unchanged(settings):
    process = render_runner(settings)
    assert process.policy.error_prefix == "render"
    assert process.policy.timeout_seconds == 5
    assert process.policy.output_bytes == 4096
    assert process.policy.control_seconds == 0.05
    assert process.policy.terminate_grace_seconds == 0.1
    default = ProcessRunner(settings, Event())
    assert default.policy == ProcessPolicy.for_preview(settings)
    assert default.policy.error_prefix == "preview"


def test_real_render_child_is_not_limited_by_preview_budget(settings):
    result = render_runner(settings).run(python("import time; time.sleep(.05); print('render')"))
    assert result.returncode == 0
    assert result.stdout == b"render\n"


@pytest.mark.parametrize("final", [False, True], ids=["preview", "render"])
def test_allocator_policy_is_owned_and_parent_private_environment_is_absent(
    settings, monkeypatch, final
):
    monkeypatch.setenv("MALLOC_ARENA_MAX", "999")
    monkeypatch.setenv("EDIVO_QA_PRIVATE_TOKEN", "synthetic-private-value")
    process = render_runner(settings) if final else ProcessRunner(settings, Event())
    result = process.run(
        python(
            "import json,os; print(json.dumps({"
            "'arenas':os.environ.get('MALLOC_ARENA_MAX'),"
            "'private':os.environ.get('EDIVO_QA_PRIVATE_TOKEN')}))"
        )
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"arenas": "2" if final else None, "private": None}


def test_render_resource_limits_are_effective_in_real_child(settings):
    result = render_runner(settings).run(
        python(
            "import json,resource; "
            "print(json.dumps({name:resource.getrlimit(getattr(resource,name)) "
            "for name in ('RLIMIT_AS','RLIMIT_CPU','RLIMIT_FSIZE','RLIMIT_NOFILE')}))"
        )
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "RLIMIT_AS": [256 * 1024**2, 256 * 1024**2],
        "RLIMIT_CPU": [3, 4],
        "RLIMIT_FSIZE": [8192, 8192],
        "RLIMIT_NOFILE": [128, 128],
    }


def test_render_address_space_limit_blocks_real_oversized_allocation(settings):
    result = render_runner(settings, render_memory_bytes=64 * 1024**2).run(
        python("bytearray(256*1024**2)")
    )
    assert result.returncode != 0


def test_render_file_size_limit_stops_real_oversized_artifact(settings, tmp_path):
    result = render_runner(settings).run(
        python("open('artifact','wb').write(b'x'*16000)"), cwd=tmp_path
    )
    assert result.returncode != 0
    assert (tmp_path / "artifact").stat().st_size == 8192


def test_render_cpu_limit_terminates_busy_child(settings):
    result = render_runner(settings, render_cpu_seconds=1).run(python("while True: pass"))
    assert result.returncode in (-signal.SIGXCPU, -signal.SIGKILL)


def test_render_timeout_terminates_and_reaps_child(settings, tmp_path):
    process = render_runner(settings)
    started = monotonic()
    with pytest.raises(PreviewProcessError, match="^render_timeout$"):
        process.run(
            python(
                "import os,pathlib,time; pathlib.Path('pid').write_text(str(os.getpid()));"
                "time.sleep(30)"
            ),
            cwd=tmp_path,
            timeout=0.25,
        )
    assert monotonic() - started < 3
    with pytest.raises(ProcessLookupError):
        os.kill(int((tmp_path / "pid").read_text()), 0)


def test_render_deadline_includes_preparation_and_multiple_processes(settings):
    process = render_runner(settings, render_timeout_seconds=0.3)
    assert process.run(python("print('prepared')")).returncode == 0
    sleep(0.35)
    with pytest.raises(PreviewProcessError, match="^render_timeout$"):
        process.pulse()


def test_incremental_stdout_can_exceed_stderr_budget_without_being_retained(settings):
    lengths = []
    result = render_runner(settings).run(
        python("import sys; sys.stdout.buffer.write(b'x'*100000)"),
        stdout_consumer=lambda data: lengths.append(len(data)),
        stdout_limit=100000,
    )
    assert result.returncode == 0
    assert result.stdout == b""
    assert sum(lengths) == 100000
    assert max(lengths) <= 4097


def test_streamed_stdout_and_private_stderr_have_independent_bounded_budgets(settings):
    count = []
    result = render_runner(settings).run(
        python("import sys; sys.stdout.write('x'*4096); sys.stderr.write('y'*4096)"),
        stdout_consumer=lambda data: count.append(len(data)),
        stdout_limit=4096,
    )
    assert result.returncode == 0
    assert sum(count) == 4096
    assert result.stdout == b""
    assert not hasattr(result, "stderr")


@pytest.mark.parametrize("pipe", ["stdout", "stderr"])
def test_each_streaming_budget_is_enforced(settings, pipe):
    with pytest.raises(PreviewProcessError, match="^render_process_output_exceeded$"):
        render_runner(settings).run(
            python(f"import sys; sys.{pipe}.write('x'*5000)"),
            stdout_consumer=lambda _chunk: None,
            stdout_limit=4096,
        )


def test_nonstreaming_render_output_still_has_combined_limit(settings):
    with pytest.raises(PreviewProcessError, match="^render_process_output_exceeded$"):
        render_runner(settings).run(
            python("import sys; sys.stdout.write('x'*3000); sys.stderr.write('y'*3000)")
        )


@pytest.mark.parametrize("overrides", [{"stdout_limit": 2}, {"stdout_limit": 0}, {"timeout": 0}])
def test_invalid_streaming_execution_configuration_is_sanitized(settings, overrides):
    with pytest.raises(PreviewProcessError, match="^render_process_configuration_invalid$"):
        render_runner(settings).run(python("print('must not spawn')"), **overrides)


def test_progress_callback_failure_closes_pipes_and_reaps_real_child(
    settings, tmp_path, monkeypatch
):
    spawned = []
    real_popen = subprocess.Popen

    def record_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        spawned.append(process)
        return process

    def fail(_chunk):
        raise RuntimeError("progress database unavailable")

    monkeypatch.setattr("app.worker.supervisor.subprocess.Popen", record_popen)
    with pytest.raises(RuntimeError, match="progress database unavailable"):
        render_runner(settings).run(
            python("import sys,time; print('progress=continue',flush=True); time.sleep(30)"),
            stdout_consumer=fail,
        )
    assert len(spawned) == 1
    assert spawned[0].poll() is not None
    assert spawned[0].stdout.closed
    assert spawned[0].stderr.closed
    with pytest.raises(ProcessLookupError):
        os.kill(spawned[0].pid, 0)


def test_cancel_is_polled_independently_of_heartbeat_and_stops_child(settings, tmp_path):
    process = render_runner(settings)
    heartbeats = []
    process.heartbeat = lambda: heartbeats.append(monotonic()) or True

    def control():
        if (tmp_path / "pid").exists():
            raise RenderCanceled()

    process.control = control
    started = monotonic()
    with pytest.raises(RenderCanceled, match="^render_canceled$"):
        process.run(
            python(
                "import os,pathlib,time; pathlib.Path('pid').write_text(str(os.getpid()));"
                "time.sleep(30)"
            ),
            cwd=tmp_path,
        )
    assert monotonic() - started < 2
    assert len(heartbeats) == 1
    with pytest.raises(ProcessLookupError):
        os.kill(int((tmp_path / "pid").read_text()), 0)


def test_cancel_remains_observable_after_child_closes_both_pipes(settings, tmp_path):
    process = render_runner(settings)

    def control():
        if (tmp_path / "pid").exists():
            raise RenderCanceled()

    process.control = control
    with pytest.raises(RenderCanceled):
        process.run(
            python(
                "import os,pathlib,time; pathlib.Path('pid').write_text(str(os.getpid()));"
                "os.close(1); os.close(2); time.sleep(30)"
            ),
            cwd=tmp_path,
        )
    with pytest.raises(ProcessLookupError):
        os.kill(int((tmp_path / "pid").read_text()), 0)


def test_lost_lease_stops_render_child(settings, tmp_path):
    process = render_runner(settings, render_heartbeat_seconds=0.05)
    process.heartbeat = lambda: not (tmp_path / "pid").exists()
    with pytest.raises(LeaseLost):
        process.run(
            python(
                "import os,pathlib,time; pathlib.Path('pid').write_text(str(os.getpid()));"
                "time.sleep(30)"
            ),
            cwd=tmp_path,
        )
    with pytest.raises(ProcessLookupError):
        os.kill(int((tmp_path / "pid").read_text()), 0)


def test_cancel_terminates_term_ignoring_descendant_without_touching_unrelated_process(
    settings, tmp_path
):
    unrelated = subprocess.Popen(python("import time; time.sleep(30)"))
    process = render_runner(settings)

    def control():
        if (tmp_path / "child").exists():
            raise RenderCanceled()

    process.control = control
    try:
        with pytest.raises(RenderCanceled):
            process.run(
                python(
                    "import os,pathlib,time,signal; child=os.fork();"
                    "os._exit(0) if child else None;"
                    "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                    "pathlib.Path('child').write_text(str(os.getpid())); time.sleep(30)"
                ),
                cwd=tmp_path,
            )
        assert_child_stopped(int((tmp_path / "child").read_text()))
        assert unrelated.poll() is None
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)


def test_stop_before_start_does_not_launch_child(settings, monkeypatch):
    def forbidden_popen(*_args, **_kwargs):
        pytest.fail("a stopped worker must not launch a child")

    monkeypatch.setattr("app.worker.supervisor.subprocess.Popen", forbidden_popen)
    process = render_runner(settings)
    process.stopped.set()
    with pytest.raises(ProcessStopped):
        process.run(python("print('must not start')"))


def test_real_sigterm_with_render_policy_stops_only_its_child(tmp_path):
    child_pid = tmp_path / "child-pid"
    code = """
import signal,sys
from threading import Event
from app.core.config import Settings
from app.worker.supervisor import ProcessPolicy,ProcessRunner,ProcessStopped
stopped=Event()
signal.signal(signal.SIGTERM,lambda *_:stopped.set())
settings=Settings(_env_file=None,database_url='postgresql+psycopg://test:only-test@127.0.0.1/qa')
try:
    ProcessRunner(settings,stopped,policy=ProcessPolicy.for_render(settings)).run(
        [sys.executable,'-c',"import os,sys,time,pathlib;"
         "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));time.sleep(30)",sys.argv[1]])
except ProcessStopped:
    raise SystemExit(0)
raise SystemExit(2)
"""
    process = subprocess.Popen(
        python(code) + [str(child_pid)], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        deadline = monotonic() + 5
        while not child_pid.exists() and monotonic() < deadline:
            sleep(0.01)
        assert child_pid.exists()
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, (stdout, stderr)
        with pytest.raises(ProcessLookupError):
            os.kill(int(child_pid.read_text()), 0)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
