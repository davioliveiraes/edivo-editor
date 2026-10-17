import subprocess

import pytest

from app.integrations.ffmpeg.capabilities import CapabilityDetector


def inventory(argv, **kwargs):
    assert "shell" not in kwargs
    assert kwargs["timeout"] > 0
    assert kwargs["stdin"] == subprocess.DEVNULL
    if "-version" in argv:
        name = "ffprobe" if "ffprobe" in argv[0] else "ffmpeg"
        return subprocess.CompletedProcess(argv, 0, stdout=f"{name} version 7.1.1 Copyright")
    if "-encoders" in argv:
        return subprocess.CompletedProcess(argv, 0, stdout=" V....D libx264 H.264\n A..... aac AAC")
    return subprocess.CompletedProcess(
        argv, 0, stdout=" ..C scale V->V\n ..C crop V->V\n ... pad V->V\n T.C drawtext V->V"
    )


def test_runtime_inventory_and_cache(settings, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return inventory(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    detector = CapabilityDetector(settings)
    report = detector.get()
    assert report.status == "available"
    assert report.ffmpeg.version == "7.1.1"
    assert report.ffprobe.version == "7.1.1"
    assert report.encoders.h264_encoder == "libx264"
    assert report.filters.drawtext.available
    assert len(calls) == 4
    report.ffmpeg.version = "modified"
    assert detector.get().ffmpeg.version == "7.1.1"
    assert len(calls) == 4
    detector._cached_at = -10000
    detector.get()
    assert len(calls) == 8


@pytest.mark.parametrize(
    "failure,reason",
    [
        (FileNotFoundError("/private/path"), "not_found"),
        (subprocess.TimeoutExpired("private", 1), "timeout"),
        (PermissionError("private"), "command_failed"),
    ],
)
def test_subprocess_failures_are_explicit(settings, monkeypatch, failure, reason):
    def run(*args, **kwargs):
        raise failure

    monkeypatch.setattr(subprocess, "run", run)
    report = CapabilityDetector(settings).get()
    assert report.status == "unavailable"
    assert report.ffmpeg.reason == reason
    assert report.ffprobe.reason == reason
    assert "private" not in report.model_dump_json()


def test_encoder_is_not_inferred_from_decoder_or_description(settings, monkeypatch):
    def run(argv, **kwargs):
        if "-encoders" in argv:
            return subprocess.CompletedProcess(
                argv, 0, stdout=" V....D h264_nvenc description mentions libx264\n A..... aac AAC"
            )
        return inventory(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    report = CapabilityDetector(settings).get()
    assert not report.encoders.h264.available
    assert report.encoders.h264.reason == "not_detected"
    assert report.encoders.aac.available
    assert report.status == "unavailable"


def test_filter_inventory_failure_does_not_report_available(settings, monkeypatch):
    def run(argv, **kwargs):
        if "-filters" in argv:
            return subprocess.CompletedProcess(argv, 1, stdout="private", stderr="secret")
        return inventory(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    report = CapabilityDetector(settings).get()
    assert not report.filters.scale.available
    assert report.filters.scale.reason == "command_failed"
    assert "secret" not in report.model_dump_json()


def test_unrecognized_version_is_not_reported(settings, monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout="private output"),
    )
    report = CapabilityDetector(settings).get()
    assert report.ffmpeg.reason == "invalid_output"
    assert report.ffmpeg.version is None


def test_worker_reports_its_own_runtime(settings, monkeypatch):
    monkeypatch.setattr(subprocess, "run", inventory)
    assert CapabilityDetector(settings, runtime="worker").get().runtime == "worker"
