"""Real POSIX children; these tests never encode user media or claim preview jobs."""

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from threading import Event, Timer
from time import monotonic, sleep

import pytest

from app.worker.supervisor import (
    LeaseLost,
    PreviewProcessError,
    ProcessRunner,
    ProcessStopped,
)


def runner(settings, **overrides):
    configured = settings.model_copy(
        update={
            "preview_ffmpeg_timeout_seconds": 5,
            "preview_process_output_bytes": 4096,
            "preview_memory_bytes": 512 * 1024**2,
            "preview_cpu_seconds": 3,
            "preview_max_bytes": 4096,
            "preview_heartbeat_seconds": 0.1,
            **overrides,
        }
    )
    return ProcessRunner(configured, Event())


def python(code):
    return [sys.executable, "-c", code]


def test_process_result_and_private_stderr(settings):
    result = runner(settings).run(
        python("import sys; print('safe'); print('/private/secret',file=sys.stderr)")
    )
    assert result.returncode == 0
    assert result.stdout == b"safe\n"
    assert not hasattr(result, "stderr")


@pytest.mark.parametrize("pipe", ["stdout", "stderr"])
def test_both_output_streams_share_one_limit(settings, pipe):
    with pytest.raises(PreviewProcessError, match="preview_process_output_exceeded"):
        runner(settings).run(python(f"import sys; sys.{pipe}.write('x'*10000)"))


def test_combined_output_limit(settings):
    with pytest.raises(PreviewProcessError, match="preview_process_output_exceeded"):
        runner(settings).run(
            python("import sys; sys.stdout.write('x'*3000); sys.stderr.write('y'*3000)")
        )


def test_timeout_kills_and_waits_for_child(settings, tmp_path):
    pid_file = tmp_path / "pid"
    started = monotonic()
    with pytest.raises(PreviewProcessError, match="preview_timeout"):
        runner(settings).run(
            python(
                "import os,time,pathlib; "
                "pathlib.Path('pid').write_text(str(os.getpid())); time.sleep(30)"
            ),
            cwd=tmp_path,
            timeout=0.3,
        )
    assert monotonic() - started < 3
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


def test_deadline_is_shared_across_calls_and_preparation(settings):
    process = runner(settings, preview_ffmpeg_timeout_seconds=0.3)
    assert process.run(python("print('first')")).returncode == 0
    sleep(0.35)
    with pytest.raises(PreviewProcessError, match="preview_timeout"):
        process.pulse()
    with pytest.raises(PreviewProcessError, match="preview_timeout"):
        process.run(python("print('must not spawn')"))


def test_heartbeat_throttled_and_lease_loss_stops_process(settings):
    process = runner(settings)
    calls = []

    def heartbeat():
        calls.append(monotonic())
        return len(calls) < 2

    process.heartbeat = heartbeat
    process.pulse()
    for _ in range(100):
        process.pulse()
    assert len(calls) == 1
    with pytest.raises(LeaseLost):
        process.run(python("import time; time.sleep(30)"))
    assert len(calls) == 2


def test_stop_before_start_and_during_running_child(settings):
    process = runner(settings)
    process.stopped.set()
    with pytest.raises(ProcessStopped):
        process.run(python("print('must not spawn')"))
    process = runner(settings)
    timer = Timer(0.15, process.stopped.set)
    timer.start()
    try:
        with pytest.raises(ProcessStopped):
            process.run(python("import time; time.sleep(30)"))
    finally:
        timer.join()


def test_pinned_descriptor_is_inherited_through_resource_launcher(settings, tmp_path):
    source = tmp_path / "input"
    source.write_bytes(b"pinned source bytes")
    with source.open("rb") as stream:
        descriptor = stream.fileno()
        result = runner(settings).run(
            python(f"import os; print(os.read({descriptor},1024).decode())"),
            pass_fds=(descriptor,),
        )
    assert result.returncode == 0
    assert result.stdout == b"pinned source bytes\n"


def test_resource_limits_are_effective_in_child(settings):
    result = runner(settings).run(
        python(
            "import json,resource; "
            "print(json.dumps({name:resource.getrlimit(getattr(resource,name)) "
            "for name in ('RLIMIT_CPU','RLIMIT_AS','RLIMIT_FSIZE','RLIMIT_NOFILE')}))"
        )
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "RLIMIT_CPU": [3, 4],
        "RLIMIT_AS": [512 * 1024**2, 512 * 1024**2],
        "RLIMIT_FSIZE": [4096, 4096],
        "RLIMIT_NOFILE": [128, 128],
    }


def test_file_size_limit_prevents_unbounded_artifact(settings, tmp_path):
    result = runner(settings).run(python("open('artifact','wb').write(b'x'*10000)"), cwd=tmp_path)
    assert result.returncode != 0
    assert (tmp_path / "artifact").stat().st_size <= 4096


def test_cpu_limit_terminates_busy_child(settings):
    result = runner(settings, preview_cpu_seconds=1).run(python("while True: pass"))
    assert result.returncode in (-signal.SIGXCPU, -signal.SIGKILL)


def test_parent_exited_zero_with_descendant_holding_pipe_is_terminated(settings, tmp_path):
    """The leader exits first; poll()!=None must not skip killing its group."""
    code = (
        "import os,time,pathlib,signal; child=os.fork(); "
        "pathlib.Path('child').write_text(str(child)) if child else None; "
        "os._exit(0) if child else None; "
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)"
    )
    with pytest.raises(PreviewProcessError, match="preview_timeout"):
        runner(settings).run(python(code), cwd=tmp_path, timeout=0.25)
    child = int((tmp_path / "child").read_text())
    process_stat = Path(f"/proc/{child}/stat")

    def child_is_running():
        try:
            return process_stat.read_text().split()[2] != "Z"
        except (FileNotFoundError, ProcessLookupError):
            return False

    # A zombie can briefly await host init/Tini, but cannot remain executing.
    deadline = monotonic() + 1
    while child_is_running() and monotonic() < deadline:
        sleep(0.01)
    assert not child_is_running()


def test_nonzero_return_is_preserved_for_compositor_validation(settings):
    assert runner(settings).run(python("raise SystemExit(7)")).returncode == 7


def test_error_codes_cannot_leak_external_exception_content():
    error = PreviewProcessError("private-password /home/private/file")
    assert str(error) == error.code == "preview_processing_failed"


def test_invalid_execution_configuration_is_sanitized(settings):
    with pytest.raises(PreviewProcessError, match="preview_process_configuration_invalid"):
        runner(settings).run([])


def test_child_environment_excludes_database_secrets_and_output_is_private(settings, tmp_path):
    result = runner(settings).run(
        python(
            "import os,json,pathlib; pathlib.Path('private').write_text('data'); "
            "print(json.dumps({'database':os.getenv('EDIVO_DATABASE_URL'), "
            "'mode':oct(pathlib.Path('private').stat().st_mode&0o777)}))"
        ),
        cwd=tmp_path,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"database": None, "mode": "0o600"}


def test_real_sigterm_stops_runner_and_its_child(tmp_path):
    child_pid = tmp_path / "child-pid"
    code = """
import signal,sys
from threading import Event
from app.core.config import Settings
from app.worker.supervisor import ProcessRunner,ProcessStopped
stopped=Event()
signal.signal(signal.SIGTERM,lambda *_:stopped.set())
settings=Settings(_env_file=None,database_url='postgresql+psycopg://test:only-test@127.0.0.1/qa')
try:
    ProcessRunner(settings,stopped).run([sys.executable,'-c',
        "import os,sys,time,pathlib;"
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));time.sleep(30)",
        sys.argv[1]])
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
