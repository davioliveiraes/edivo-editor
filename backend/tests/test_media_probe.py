import copy
import json
import struct
import sys
from time import monotonic

import pytest

from app.core.errors import AppError
from app.integrations.ffmpeg.probe import (
    FFprobeInspector,
    normalize_metadata,
    run_bounded,
    validate_mp4_structure,
)


@pytest.fixture
def probe_payload():
    return {
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "1.2345"},
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 320,
                "height": 180,
                "avg_frame_rate": "30000/1001",
                "r_frame_rate": "30/1",
                "duration": "1.2",
                "disposition": {"default": 1, "attached_pic": 0},
                "tags": {"GPS": "private"},
            },
            {"index": 1, "codec_type": "audio", "codec_name": "aac"},
        ],
        "frames": [{"media_type": "video", "stream_index": 0}],
        "tags": {"filename": "/private/source.mp4"},
    }


def box(kind, payload=b""):
    return struct.pack(">I4s", len(payload) + 8, kind) + payload


@pytest.fixture
def structured_mp4(tmp_path):
    path = tmp_path / "structure.mp4"
    path.write_bytes(
        box(b"ftyp", b"isom" + b"\x00" * 4 + b"mp42") + box(b"moov") + box(b"mdat", b"x")
    )
    return path


def test_normalization_rational_duration_and_private_filter(probe_payload):
    result = normalize_metadata(probe_payload, 600)
    assert (result.fps_num, result.fps_den) == (30000, 1001)
    assert result.duration_ms == 1235
    assert result.has_audio is True and result.audio_codec == "aac"
    assert "private" not in json.dumps(result.probe_metadata)
    assert "tags" not in result.probe_metadata


def test_video_without_audio_and_frame_rate_fallback(probe_payload):
    probe_payload["streams"] = probe_payload["streams"][:1]
    probe_payload["streams"][0]["avg_frame_rate"] = "0/0"
    result = normalize_metadata(probe_payload, 600)
    assert result.has_audio is False and result.audio_codec is None
    assert (result.fps_num, result.fps_den) == (30, 1)


def test_selection_excludes_cover_art_and_prefers_default(probe_payload):
    cover = copy.deepcopy(probe_payload["streams"][0])
    cover["disposition"]["attached_pic"] = 1
    cover["index"] = 0
    ordinary = copy.deepcopy(probe_payload["streams"][0])
    ordinary["index"] = 1
    ordinary["disposition"]["default"] = 0
    preferred = copy.deepcopy(probe_payload["streams"][0])
    preferred["index"] = 2
    probe_payload["streams"] = [cover, ordinary, preferred]
    assert normalize_metadata(probe_payload, 600).probe_metadata["video_stream_index"] == 2


@pytest.mark.parametrize("duration", ["NaN", "Infinity", "-1", "0", "unknown", None])
def test_invalid_durations(probe_payload, duration):
    probe_payload["format"]["duration"] = duration
    probe_payload["streams"][0]["duration"] = duration
    with pytest.raises(AppError) as error:
        normalize_metadata(probe_payload, 600)
    assert error.value.status_code == 422


def test_duration_limit_uses_unrounded_seconds(probe_payload):
    probe_payload["format"]["duration"] = "600.0001"
    with pytest.raises(AppError) as error:
        normalize_metadata(probe_payload, 600)
    assert error.value.code == "media_duration_exceeded"


@pytest.mark.parametrize(
    "field,value",
    [
        ("width", 0),
        ("height", -1),
        ("width", "NaN"),
        ("width", True),
        ("codec_name", "../../private"),
        ("codec_name", "a" * 65),
        ("avg_frame_rate", "0/0"),
        ("avg_frame_rate", "NaN"),
    ],
)
def test_invalid_stream_values(probe_payload, field, value):
    probe_payload["streams"][0][field] = value
    if field == "avg_frame_rate":
        probe_payload["streams"][0]["r_frame_rate"] = "0/0"
    with pytest.raises(AppError):
        normalize_metadata(probe_payload, 600)


@pytest.mark.parametrize(
    "payload", [None, [], {}, {"streams": [], "format": {"format_name": "mp4"}}]
)
def test_invalid_probe_structure(payload):
    with pytest.raises(AppError):
        normalize_metadata(payload, 600)


def test_structural_walk_accepts_mp4(structured_mp4):
    validate_mp4_structure(structured_mp4)


@pytest.mark.parametrize(
    "data,status",
    [
        (b"fakeftypisom", 422),
        (box(b"free", b"ftypisom"), 415),
        (box(b"ftyp", b"qt  " + b"\x00" * 4) + box(b"moov") + box(b"mdat", b"x"), 415),
        (box(b"ftyp", b"isom" + b"\x00" * 4), 422),
        (struct.pack(">I4s", 999, b"ftyp") + b"isom", 422),
        (struct.pack(">I4s", 1, b"ftyp") + b"1234", 422),
    ],
)
def test_fake_and_corrupt_structure(tmp_path, data, status):
    path = tmp_path / "fake.mp4"
    path.write_bytes(data)
    with pytest.raises(AppError) as error:
        validate_mp4_structure(path)
    assert error.value.status_code == status


def test_bounded_process_success():
    code, output = run_bounded([sys.executable, "-c", "print('ok')"], 2, 1024)
    assert code == 0 and output == b"ok\n"


@pytest.mark.parametrize("pipe", ["stdout", "stderr"])
def test_bounded_process_caps_both_pipes(pipe):
    with pytest.raises(AppError) as error:
        run_bounded([sys.executable, "-c", f"import sys; sys.{pipe}.write('x'*100000)"], 2, 1024)
    assert error.value.status_code == 422


def test_bounded_process_times_out_and_reaps():
    started = monotonic()
    with pytest.raises(AppError) as error:
        run_bounded([sys.executable, "-c", "import time; time.sleep(30)"], 0.1, 1024)
    assert error.value.code == "probe_timeout"
    assert monotonic() - started < 2


def test_bounded_process_missing_binary():
    with pytest.raises(AppError) as error:
        run_bounded(["/not-a-real-test-binary"], 1, 1024)
    assert error.value.code == "probe_unavailable"


def test_inspector_safe_arguments_and_private_metadata(
    monkeypatch, settings, structured_mp4, probe_payload
):
    calls = []

    def run(argv, timeout, limit):
        calls.append((argv, timeout, limit))
        return 0, json.dumps(probe_payload).encode()

    monkeypatch.setattr("app.integrations.ffmpeg.probe.run_bounded", run)
    result = FFprobeInspector(settings).inspect(structured_mp4)
    argv, timeout, limit = calls[0]
    assert argv[argv.index("-protocol_whitelist") + 1] == "file"
    assert argv[argv.index("-format_whitelist") + 1] == "mov"
    assert argv[argv.index("-enable_drefs") + 1] == "0"
    assert argv[-1] == str(structured_mp4.absolute())
    assert (
        timeout == settings.ffprobe_timeout_seconds and limit == settings.ffprobe_max_output_bytes
    )
    assert result.width == 320


@pytest.mark.parametrize("code,output", [(1, b"{}"), (0, b"not-json"), (0, b'{"frames":[]}')])
def test_inspector_rejects_bad_process_output(monkeypatch, settings, structured_mp4, code, output):
    monkeypatch.setattr("app.integrations.ffmpeg.probe.run_bounded", lambda *args: (code, output))
    with pytest.raises(AppError) as error:
        FFprobeInspector(settings).inspect(structured_mp4)
    assert error.value.status_code == 422
