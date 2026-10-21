"""Unit tests use synthetic framing and mocked FFprobe, never claim real decoding."""

import json
from dataclasses import asdict
from fractions import Fraction
from math import ceil

import pytest

from app.core.errors import AppError
from app.integrations.ffmpeg.audio_probe import (
    FFprobeAudioInspector,
    normalize_audio_metadata,
    validate_mp3_structure,
)


def frame(header=b"\xff\xfb\x90\x00", length=417):
    """A complete MPEG-1 Layer III frame boundary, with deliberately dummy payload."""
    return header + b"\x00" * (length - 4)


def id3_tag(payload=b"", *, version=4, flags=0):
    size = len(payload)
    encoded_size = bytes((size >> 21 & 127, size >> 14 & 127, size >> 7 & 127, size & 127))
    return b"ID3" + bytes((version, 0, flags)) + encoded_size + payload


@pytest.fixture
def structured_mp3(tmp_path):
    path = tmp_path / "structure.mp3"
    path.write_bytes(id3_tag(b"metadata") + frame() * 47)
    return path


@pytest.fixture
def audio_probe_payload():
    return {
        "format": {"format_name": "mp3", "duration": "1.2345"},
        "streams": [
            {
                "index": 0,
                "codec_type": "audio",
                "codec_name": "mp3",
                "sample_rate": "44100",
                "channels": 2,
                "duration": "1.2",
                "tags": {"title": "private title"},
            }
        ],
        "frames": [{"media_type": "audio", "stream_index": 0, "nb_samples": 1152}] * 47,
        "tags": {"filename": "/private/source.mp3"},
    }


def test_normalizes_duration_and_keeps_only_public_fields(audio_probe_payload):
    metadata = normalize_audio_metadata(audio_probe_payload, 600)
    assert asdict(metadata) == {
        "duration_ms": 1235,
        "codec": "mp3",
        "sample_rate": 44100,
        "channels": 2,
    }
    assert "private" not in json.dumps(asdict(metadata))


def test_accepts_mono_and_uses_available_stream_duration(audio_probe_payload):
    audio_probe_payload["format"]["duration"] = "N/A"
    audio_probe_payload["streams"][0]["channels"] = 1
    metadata = normalize_audio_metadata(audio_probe_payload, 600)
    assert metadata.channels == 1 and metadata.duration_ms == 1200


@pytest.mark.parametrize("duration", ["NaN", "Infinity", "-1", "0", "unknown", None, True])
def test_rejects_invalid_or_missing_durations(audio_probe_payload, duration):
    audio_probe_payload["format"]["duration"] = duration
    audio_probe_payload["streams"][0]["duration"] = duration
    with pytest.raises(AppError) as error:
        normalize_audio_metadata(audio_probe_payload, 600)
    assert (error.value.status_code, error.value.code) == (422, "invalid_audio")


def test_duration_limit_precedes_rounding_and_checks_both_values(audio_probe_payload):
    audio_probe_payload["streams"][0]["duration"] = "600.0001"
    with pytest.raises(AppError) as error:
        normalize_audio_metadata(audio_probe_payload, 600)
    assert error.value.code == "audio_duration_exceeded"


@pytest.mark.parametrize(
    "field,value",
    [
        ("sample_rate", 0),
        ("sample_rate", True),
        ("sample_rate", "44100.0"),
        ("sample_rate", 96000),
        ("sample_rate", 12345),
        ("channels", 0),
        ("channels", True),
        ("channels", 3),
        ("channels", "NaN"),
        ("index", -1),
        ("index", False),
        ("index", "../private"),
    ],
)
def test_rejects_malformed_stream_values(audio_probe_payload, field, value):
    audio_probe_payload["streams"][0][field] = value
    with pytest.raises(AppError) as error:
        normalize_audio_metadata(audio_probe_payload, 600)
    assert error.value.code == "invalid_audio"


@pytest.mark.parametrize("kind", ["video", "audio", "data", "subtitle", "attachment"])
def test_rejects_every_additional_stream_including_cover_art(audio_probe_payload, kind):
    audio_probe_payload["streams"].append(
        {"index": 1, "codec_type": kind, "codec_name": "mjpeg", "disposition": {"attached_pic": 1}}
    )
    with pytest.raises(AppError) as error:
        normalize_audio_metadata(audio_probe_payload, 600)
    assert (error.value.status_code, error.value.code) == (415, "unsupported_audio")


@pytest.mark.parametrize("field,value", [("codec_type", "video"), ("codec_name", "aac")])
def test_rejects_non_mp3_audio_stream(audio_probe_payload, field, value):
    audio_probe_payload["streams"][0][field] = value
    with pytest.raises(AppError) as error:
        normalize_audio_metadata(audio_probe_payload, 600)
    assert error.value.code == "unsupported_audio"


@pytest.mark.parametrize("format_name", ["wav", "mov,mp4,m4a,3gp,3g2,mj2", "mp3,mp4", None])
def test_rejects_other_containers(audio_probe_payload, format_name):
    audio_probe_payload["format"]["format_name"] = format_name
    with pytest.raises(AppError) as error:
        normalize_audio_metadata(audio_probe_payload, 600)
    assert error.value.code == "unsupported_audio"


@pytest.mark.parametrize(
    "payload", [None, [], {}, {"format": {}}, {"format": {"format_name": "mp3"}, "streams": []}]
)
def test_rejects_malformed_probe_payload(payload):
    with pytest.raises(AppError) as error:
        normalize_audio_metadata(payload, 600)
    assert error.value.code == "invalid_audio"


@pytest.mark.parametrize("version", [2, 3, 4])
def test_accepts_id3v2_and_trailing_id3v1(tmp_path, version):
    path = tmp_path / "tags.mp3"
    path.write_bytes(id3_tag(b"metadata", version=version) + frame() + b"TAG" + b"\x00" * 125)
    validate_mp3_structure(path)


def test_accepts_id3v24_footer(tmp_path):
    tag = id3_tag(b"metadata", flags=0x10)
    path = tmp_path / "footer.mp3"
    path.write_bytes(tag + b"3DI" + tag[3:10] + frame())
    validate_mp3_structure(path)


@pytest.mark.parametrize(
    "header,length",
    [
        (b"\xff\xfb\x90\x00", 417),  # MPEG-1, 128 kbit/s, 44.1 kHz
        (b"\xff\xfb\x92\x00", 418),  # Padding byte
        (b"\xff\xf3\x80\xc0", 208),  # MPEG-2, 64 kbit/s, 22.05 kHz, mono
        (b"\xff\xe3\x80\xc0", 417),  # MPEG-2.5, 64 kbit/s, 11.025 kHz
    ],
)
def test_accepts_mp3_without_id3(tmp_path, header, length):
    path = tmp_path / "frames.mp3"
    path.write_bytes(frame(header, length) * 2)
    validate_mp3_structure(path)


def test_accepts_variable_bitrate_and_xing_payload(tmp_path):
    first = frame()
    first = first[:36] + b"Xing" + first[40:]
    path = tmp_path / "variable.mp3"
    path.write_bytes(first + frame(b"\xff\xfb\xa0\x00", 522) + frame())
    validate_mp3_structure(path)


@pytest.mark.parametrize(
    "data,code",
    [
        (b"", "invalid_audio"),
        (b"ID3", "invalid_audio"),
        (b"RIFF" + b"\x00" * 400, "unsupported_audio"),
        (b"\x00\x00\x00\x18ftypisom", "unsupported_audio"),
        (id3_tag(), "invalid_audio"),
        (id3_tag(b"garbage") + b"garbage", "invalid_audio"),
        (b"ID3\x04\x00\x00\x80\x00\x00\x00" + frame(), "invalid_audio"),
        (b"ID3\x04\x00\x00\x00\x00\x7f\x7f" + frame(), "invalid_audio"),
        (id3_tag(version=5) + frame(), "invalid_audio"),
        (id3_tag(version=3, flags=0x10) + frame(), "invalid_audio"),
        (id3_tag(flags=0x10) + frame(), "invalid_audio"),
        (frame()[:-1], "invalid_audio"),
        (frame() + frame()[:-1], "invalid_audio"),
        (frame() + b"trailing junk", "invalid_audio"),
        (frame(b"\xff\xfd\x90\x00"), "invalid_audio"),  # Layer II
        (frame(b"\xff\xeb\x90\x00"), "invalid_audio"),  # Reserved MPEG version
        (frame(b"\xff\xfb\xf0\x00"), "invalid_audio"),  # Reserved bitrate
        (frame(b"\xff\xfb\x00\x00"), "invalid_audio"),  # Free format outside importer
        (frame(b"\xff\xfb\x9c\x00"), "invalid_audio"),  # Reserved sample rate
    ],
)
def test_rejects_renamed_id3_only_malformed_and_truncated_files(tmp_path, data, code):
    path = tmp_path / "bad.mp3"
    path.write_bytes(data)
    with pytest.raises(AppError) as error:
        validate_mp3_structure(path)
    assert error.value.code == code


def test_unreadable_input_has_no_private_path(tmp_path):
    path = tmp_path / "private-missing.mp3"
    with pytest.raises(AppError) as error:
        validate_mp3_structure(path)
    assert error.value.code == "invalid_audio"
    assert str(path) not in error.value.message


def test_inspector_restricts_protocol_format_output_and_decode_interval(
    monkeypatch, settings, structured_mp3, audio_probe_payload
):
    calls = []

    def run(argv, timeout, output_limit):
        calls.append((argv, timeout, output_limit))
        return 0, json.dumps(audio_probe_payload).encode()

    monkeypatch.setattr("app.integrations.ffmpeg.audio_probe.run_bounded", run)
    result = FFprobeAudioInspector(settings).inspect(structured_mp3)
    assert result.codec == "mp3" and result.duration_ms == 1235
    argv, timeout, output_limit = calls[0]
    assert argv[argv.index("-protocol_whitelist") + 1] == "file"
    assert argv[argv.index("-format_whitelist") + 1] == "mp3"
    assert argv[argv.index("-read_intervals") + 1] == "%+2"
    assert "nb_samples" in argv[argv.index("-show_entries") + 1]
    assert "-show_frames" in argv and "-select_streams" not in argv
    assert timeout == settings.ffprobe_timeout_seconds
    assert output_limit == settings.ffprobe_max_output_bytes
    assert argv[-1] == str(structured_mp3.absolute())


@pytest.mark.parametrize("code,output", [(1, b"{}"), (0, b"not-json"), (0, b"\xff"), (0, b"[]")])
def test_inspector_rejects_bad_process_output(monkeypatch, settings, structured_mp3, code, output):
    monkeypatch.setattr(
        "app.integrations.ffmpeg.audio_probe.run_bounded", lambda *args: (code, output)
    )
    with pytest.raises(AppError) as error:
        FFprobeAudioInspector(settings).inspect(structured_mp3)
    assert error.value.code == "invalid_audio"


@pytest.mark.parametrize(
    "frames",
    [
        None,
        [],
        [{}],
        ["bad"],
        [{"media_type": "audio", "stream_index": 0, "nb_samples": 1152}],
        [{"media_type": "audio", "stream_index": 1, "nb_samples": 1152}] * 47,
        [{"media_type": "audio", "stream_index": 0, "nb_samples": 0}] * 47,
        [{"media_type": "audio", "stream_index": 0, "nb_samples": True}] * 47,
        [{"media_type": "audio", "stream_index": 0, "nb_samples": "NaN"}] * 47,
        [{"media_type": "video", "stream_index": 0, "nb_samples": 1152}] * 47,
    ],
)
def test_requires_significant_decoded_audio_prefix(
    monkeypatch, settings, structured_mp3, audio_probe_payload, frames
):
    audio_probe_payload["frames"] = frames
    monkeypatch.setattr(
        "app.integrations.ffmpeg.audio_probe.run_bounded",
        lambda *args: (0, json.dumps(audio_probe_payload).encode()),
    )
    with pytest.raises(AppError) as error:
        FFprobeAudioInspector(settings).inspect(structured_mp3)
    assert error.value.code == "invalid_audio"


@pytest.mark.parametrize("duration,frame_count", [("0.05", 1), ("0.5", 19), ("30", 77)])
def test_allows_short_sources_proportionally_or_two_seconds_for_long_sources(
    monkeypatch, settings, structured_mp3, audio_probe_payload, duration, frame_count
):
    structured_mp3.write_bytes(frame() * ceil(float(duration) * 44100 / 1152))
    audio_probe_payload["format"]["duration"] = duration
    audio_probe_payload["streams"][0]["duration"] = duration
    audio_probe_payload["frames"] = audio_probe_payload["frames"][:1] * frame_count
    monkeypatch.setattr(
        "app.integrations.ffmpeg.audio_probe.run_bounded",
        lambda *args: (0, json.dumps(audio_probe_payload).encode()),
    )
    assert FFprobeAudioInspector(settings).inspect(structured_mp3).duration_ms == int(
        float(duration) * 1000
    )


@pytest.mark.parametrize(
    "status,code,expected_code",
    [
        (503, "probe_timeout", "probe_timeout"),
        (503, "probe_unavailable", "probe_unavailable"),
        (422, "invalid_media", "invalid_audio"),
    ],
)
def test_bounded_process_errors_are_sanitized_for_audio(
    monkeypatch, settings, structured_mp3, status, code, expected_code
):
    def run(*args):
        raise AppError(status, code, "private subprocess details")

    monkeypatch.setattr("app.integrations.ffmpeg.audio_probe.run_bounded", run)
    with pytest.raises(AppError) as error:
        FFprobeAudioInspector(settings).inspect(structured_mp3)
    assert error.value.status_code == status and error.value.code == expected_code
    assert "private" not in error.value.message and "vídeo" not in error.value.message


@pytest.mark.parametrize(
    "header,length,samples,rate,channels",
    [
        (b"\xff\xfb\x90\x00", 417, 1152, 44100, 2),
        (b"\xff\xfb\x94\xc0", 384, 1152, 48000, 1),
        (b"\xff\xf3\x80\xc0", 208, 576, 22050, 1),
        (b"\xff\xe3\x80\xc0", 417, 576, 11025, 1),
    ],
)
def test_structure_accumulates_exact_frame_duration(
    tmp_path, header, length, samples, rate, channels
):
    path = tmp_path / "timing.mp3"
    path.write_bytes(frame(header, length) * 7)
    structure = validate_mp3_structure(path)
    assert structure.duration == Fraction(7 * samples, rate)
    assert structure.max_frame_duration == Fraction(samples, rate)
    assert structure.sample_rates == {rate}
    assert structure.channels == {channels}


def test_structure_rejects_over_limit_before_probing(tmp_path, settings, monkeypatch):
    path = tmp_path / "over-limit.mp3"
    path.write_bytes(frame() * 500)
    settings.audio_max_duration_seconds = 1
    calls = []
    monkeypatch.setattr(
        "app.integrations.ffmpeg.audio_probe.run_bounded", lambda *args: calls.append(args)
    )
    with pytest.raises(AppError) as error:
        FFprobeAudioInspector(settings).inspect(path)
    assert error.value.code == "audio_duration_exceeded" and not calls


@pytest.mark.parametrize("declared_duration,physical_seconds", [("1", 13), ("599", 601)])
def test_forged_xing_duration_cannot_hide_frames(
    tmp_path, settings, monkeypatch, audio_probe_payload, declared_duration, physical_seconds
):
    # Dummy Xing framing reproduces the admission logic independently of FFprobe;
    # real runtime confirmation used a synthetic 661 s MP3 in a Linux memfd.
    first = bytearray(frame())
    first[36:52] = b"Xing" + (3).to_bytes(4) + (40).to_bytes(4) + (1).to_bytes(4)
    path = tmp_path / "forged-xing.mp3"
    path.write_bytes(first + frame() * ceil(physical_seconds * 44100 / 1152))
    audio_probe_payload["format"]["duration"] = declared_duration
    audio_probe_payload["streams"][0]["duration"] = declared_duration
    monkeypatch.setattr(
        "app.integrations.ffmpeg.audio_probe.run_bounded",
        lambda *args: (0, json.dumps(audio_probe_payload).encode()),
    )
    with pytest.raises(AppError) as error:
        FFprobeAudioInspector(settings).inspect(path)
    assert error.value.code == (
        "audio_duration_exceeded" if physical_seconds > 600 else "invalid_audio"
    )


def test_frame_clock_handles_changes_without_rounding_drift(tmp_path):
    path = tmp_path / "different-rates.mp3"
    path.write_bytes(frame() * 3 + frame(b"\xff\xf3\x80\xc0", 208) * 5)
    structure = validate_mp3_structure(path)
    assert structure.duration == Fraction(3 * 1152, 44100) + Fraction(5 * 576, 22050)
    assert structure.sample_rates == {44100, 22050}


def test_exact_limit_allows_only_two_frames_of_encoder_overhead(tmp_path):
    path = tmp_path / "padding.mp3"
    # 48 kHz MPEG-1 frames are exactly 24 ms each: 125 frames = 3 seconds.
    data = frame(b"\xff\xfb\x94\xc0", 384)
    path.write_bytes(data * 127)
    assert validate_mp3_structure(path, 3).duration == Fraction(3048, 1000)
    path.write_bytes(data * 128)
    with pytest.raises(AppError) as error:
        validate_mp3_structure(path, 3)
    assert error.value.code == "audio_duration_exceeded"


@pytest.mark.parametrize("sample_rate,channels", [(48000, 2), (44100, 1)])
def test_probe_stream_parameters_must_agree_with_framing(
    monkeypatch, settings, structured_mp3, audio_probe_payload, sample_rate, channels
):
    audio_probe_payload["streams"][0].update(sample_rate=str(sample_rate), channels=channels)
    monkeypatch.setattr(
        "app.integrations.ffmpeg.audio_probe.run_bounded",
        lambda *args: (0, json.dumps(audio_probe_payload).encode()),
    )
    with pytest.raises(AppError) as error:
        FFprobeAudioInspector(settings).inspect(structured_mp3)
    assert error.value.code == "invalid_audio"
