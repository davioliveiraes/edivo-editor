import signal
from types import SimpleNamespace

import pytest
from pydantic_settings import SettingsError

from app.worker import main as worker


@pytest.mark.parametrize(
    "dependencies_ok,capabilities_ok,expected",
    [(True, True, 0), (False, True, 1), (True, False, 1)],
)
def test_worker_idle_lifecycle_and_dependency_failures(
    settings, monkeypatch, dependencies_ok, capabilities_ok, expected
):
    events = []
    handlers = {}

    class Database:
        def __init__(self, _settings):
            pass

        def check(self):
            return dependencies_ok

        def close(self):
            events.append("closed")

    class Event:
        def wait(self):
            events.append("idle_wait")
            handlers[signal.SIGTERM](signal.SIGTERM, None)

        def set(self):
            events.append("signalled")

    def signal_handler(sig, handler):
        handlers[sig] = handler
        return signal.SIG_DFL

    report = SimpleNamespace(
        status="available" if capabilities_ok else "unavailable", model_dump=lambda: {}
    )
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(worker, "Database", Database)
    monkeypatch.setattr(worker, "Event", Event)
    monkeypatch.setattr(worker.signal, "signal", signal_handler)
    monkeypatch.setattr(worker.CapabilityDetector, "get", lambda _self: report)
    assert worker.run() == expected
    assert "closed" in events
    assert ("idle_wait" in events) == (expected == 0)
    assert handlers == {signal.SIGTERM: signal.SIG_DFL, signal.SIGINT: signal.SIG_DFL}


def test_worker_configuration_error_is_sanitized(monkeypatch, capsys):
    def invalid_settings():
        raise SettingsError("private-password /home/private/config")

    monkeypatch.setattr(worker, "get_settings", invalid_settings)
    assert worker.run() == 1
    output = capsys.readouterr().err
    assert "worker_configuration_invalid" in output
    assert "private-password" not in output
    assert "/home/private" not in output
