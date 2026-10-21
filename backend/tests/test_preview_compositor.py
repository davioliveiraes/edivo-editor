"""Security/contract checks and real FFmpeg composition on synthetic sources.

Real-media tests skip explicitly when FFmpeg is absent on the host. They run
unmocked in the backend runtime as part of the R04 FFmpeg acceptance matrix.
"""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest

from app.integrations.ffmpeg.font_metrics import FONT_SHA256, FontLayoutError, FontMetrics
from app.integrations.ffmpeg.preview import (
    DURATION_TOLERANCE_SECONDS,
    PROFILE_SIZES,
    render_preview,
    validate_artifact,
    validate_metadata,
)
from app.schemas.edit_spec import EditSpecV1, EditSpecV2
from app.worker.supervisor import PreviewProcessError, ProcessResult, ProcessRunner


def plan_v2(**updates):
    payload = {
        "schema_version": 2,
        "source_media_id": str(uuid4()),
        "trim": {"start_ms": 1000, "end_ms": 11000},
        "canvas": {"preset": "9:16", "fit": "cover", "focal_x": 0.25, "focal_y": 0.75},
        "text_overlays": [],
        "audio": {},
    }
    payload.update(updates)
    return EditSpecV2.model_validate(payload)


def overlay(**updates):
    payload = {
        "id": str(uuid4()),
        "text": "Ação: 'sim' \\ 100% — Olá!\nSegunda linha",
        "start_ms": 1000,
        "end_ms": 11000,
        "position": "top",
        "style": "blue_outline",
        "size": "medium",
    }
    return {**payload, **updates}


def good_metadata(*, audio=False, preset="9:16", duration_ms=1000):
    width, height = PROFILE_SIZES[preset]
    result = {
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": str(duration_ms / 1000)},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "pix_fmt": "yuv420p",
                "width": width,
                "height": height,
                "avg_frame_rate": "24/1",
                "sample_aspect_ratio": "1:1",
                "nb_frames": "24",
                "duration": str(duration_ms / 1000),
            }
        ],
    }
    if audio:
        result["streams"].append(
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "48000",
                "channels": 2,
                "duration": str(duration_ms / 1000),
            }
        )
    return result


@pytest.mark.parametrize("preset", PROFILE_SIZES)
@pytest.mark.parametrize("has_audio", [False, True])
def test_validated_profile_returns_only_controlled_metadata(preset, has_audio):
    payload = good_metadata(audio=has_audio, preset=preset)
    payload["format"]["filename"] = "/private/path/should-never-be-returned.mp4"
    payload["streams"][0]["tags"] = {"private": "not public"}
    controlled = validate_metadata(payload, preset, 1000, has_audio)
    assert controlled["has_audio"] is has_audio
    assert controlled["frame_count"] == 24
    assert controlled["pixel_format"] == "yuv420p"
    assert "private" not in json.dumps(controlled)
    assert "filename" not in controlled


@pytest.mark.parametrize(
    ("location", "key", "value"),
    [
        ("format", "duration", "NaN"),
        ("format", "duration", "Infinity"),
        ("format", "duration", "0"),
        ("format", "duration", "8.2"),
        ("format", "duration", "0.8"),
        ("format", "start_time", "0.1"),
        ("format", "start_time", "NaN"),
        ("video", "codec_name", "mpeg4"),
        ("video", "pix_fmt", "yuv444p"),
        ("video", "width", 1080),
        ("video", "height", 1920),
        ("video", "avg_frame_rate", "0/0"),
        ("video", "avg_frame_rate", "30/1"),
        ("video", "sample_aspect_ratio", "2:1"),
        ("video", "nb_frames", "0"),
        ("video", "nb_frames", "N/A"),
        ("video", "duration", "0.8"),
        ("video", "side_data_list", [{"rotation": 90}]),
        ("video", "start_time", "-0.1"),
        ("video", "start_time", "NaN"),
        ("audio", "codec_name", "mp3"),
        ("audio", "channels", 1),
        ("audio", "sample_rate", "44100"),
        ("audio", "duration", "0.5"),
    ],
)
def test_rejects_artifact_profile_mismatches_and_nonfinite_values(location, key, value):
    payload = good_metadata(audio=True)
    item = payload["format"] if location == "format" else payload["streams"][location == "audio"]
    item[key] = value
    with pytest.raises(PreviewProcessError, match="^preview_validation_failed$"):
        validate_metadata(payload, "9:16", 1000, True)


def test_requires_exact_audio_presence_and_rejects_extra_streams():
    with pytest.raises(PreviewProcessError):
        validate_metadata(good_metadata(), "9:16", 1000, True)
    with pytest.raises(PreviewProcessError):
        validate_metadata(good_metadata(audio=True), "9:16", 1000, False)
    payload = good_metadata()
    payload["streams"].append({"codec_type": "subtitle"})
    with pytest.raises(PreviewProcessError):
        validate_metadata(payload, "9:16", 1000, False)


def test_one_ms_window_is_validated_with_documented_single_frame_quantization():
    payload = good_metadata(duration_ms=1)
    payload["format"]["duration"] = "0.042000"
    payload["streams"][0].update(duration="0.041667", nb_frames="1")
    controlled = validate_metadata(payload, "9:16", 1, False)
    assert controlled["duration_ms"] == 42
    assert DURATION_TOLERANCE_SECONDS < 0.043


@pytest.fixture
def font_path():
    candidates = [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path(__file__).resolve().parents[2] / "frontend/public/fonts/DejaVuSans.ttf",
    ]
    for path in candidates:
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == FONT_SHA256:
            return path
    pytest.skip("The exact licensed Edivo overlay font is not installed in this test runtime")


def test_font_layout_fits_all_120_characters_without_truncation(font_path):
    with FontMetrics(font_path) as metrics:
        short = metrics.layout("Olá", "large", "top", True, 540, 960)
        long = metrics.layout("W" * 120, "large", "bottom", True, 540, 960)
        assert long.font_size < short.font_size
        assert max(long.line_widths) + long.border_width * 2 <= 540 * 0.86
        assert long.block_top > 960 / 2
        assert short.block_top == 960 * 0.07


def test_font_handles_accents_quotes_colons_backslashes_and_two_lines(font_path):
    with FontMetrics(font_path) as metrics:
        layout = metrics.layout(overlay()["text"], "medium", "center", True, 540, 540)
        assert len(layout.line_widths) == 2
        assert layout.line_widths[0] != layout.line_widths[1]
        assert layout.block_top + layout.line_height == pytest.approx(270)


def test_missing_modified_font_and_unsupported_glyph_fail_explicitly(font_path, tmp_path):
    with pytest.raises(FontLayoutError, match="preview_font_unavailable"):
        FontMetrics(tmp_path / "missing.ttf")
    bad = tmp_path / "wrong.ttf"
    bad.write_bytes(b"not a font")
    with pytest.raises(FontLayoutError, match="preview_font_unavailable"):
        FontMetrics(bad)
    with (
        FontMetrics(font_path) as metrics,
        pytest.raises(FontLayoutError, match="preview_text_unsupported"),
    ):
        metrics.width("\U0010ffff", 30)


class CaptureRunner:
    def __init__(self):
        self.calls = []

    def check(self):
        return None

    def run(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        return ProcessResult(1, b"")


def test_command_never_contains_text_and_textfiles_preserve_literal_content(
    settings, tmp_path, monkeypatch, font_path
):
    import app.integrations.ffmpeg.preview as compositor

    monkeypatch.setattr(compositor, "FontMetrics", lambda: FontMetrics(font_path))
    text = overlay()
    plan = plan_v2(text_overlays=[text])
    runner = CaptureRunner()
    with pytest.raises(PreviewProcessError, match="preview_encoding_failed"):
        render_preview(
            settings,
            plan,
            source_fd=11,
            source_video_stream_index=1,
            source_has_audio=False,
            background_fd=None,
            preview_start_ms=2000,
            duration_ms=1000,
            workspace=tmp_path,
            runner=runner,
            set_stage=lambda _: None,
        )
    argv, options = runner.calls[0]
    assert all(text["text"] not in argument for argument in argv)
    graph = argv[argv.index("-filter_complex") + 1]
    assert graph.count("drawtext=") == 2
    assert "expansion=none" in graph
    assert "/proc/self/fd/11" in argv
    assert options["pass_fds"] == (11,)
    assert options["cwd"] == tmp_path
    assert "shell" not in options
    assert "-n" in argv and "-y" not in argv
    assert argv[argv.index("-format_whitelist") + 1] == "mov"
    assert argv[argv.index("-enable_drefs") + 1] == "0"
    assert argv[argv.index("-use_absolute_path") + 1] == "0"
    lines = list(tmp_path.glob("overlay-*.txt"))
    assert [path.read_text() for path in sorted(lines)] == text["text"].split("\n")
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in lines)


def test_existing_textfile_is_not_overwritten(settings, tmp_path, monkeypatch, font_path):
    import app.integrations.ffmpeg.preview as compositor

    monkeypatch.setattr(compositor, "FontMetrics", lambda: FontMetrics(font_path))
    existing = tmp_path / "overlay-0-line-0.txt"
    existing.write_text("preserve")
    with pytest.raises(PreviewProcessError, match="preview_storage_unavailable"):
        render_preview(
            settings,
            plan_v2(text_overlays=[overlay()]),
            source_fd=11,
            source_video_stream_index=0,
            source_has_audio=False,
            background_fd=None,
            preview_start_ms=1000,
            duration_ms=1000,
            workspace=tmp_path,
            runner=CaptureRunner(),
            set_stage=lambda _: None,
        )
    assert existing.read_text() == "preserve"


def test_invalid_window_fails_before_spawning(settings, tmp_path):
    runner = CaptureRunner()
    with pytest.raises(PreviewProcessError, match="preview_invalid_plan"):
        render_preview(
            settings,
            plan_v2(),
            source_fd=11,
            source_video_stream_index=0,
            source_has_audio=False,
            background_fd=None,
            preview_start_ms=10999,
            duration_ms=1000,
            workspace=tmp_path,
            runner=runner,
            set_stage=lambda _: None,
        )
    assert runner.calls == []


REAL_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
real_media = pytest.mark.skipif(not REAL_FFMPEG, reason="FFmpeg/FFprobe absent on this host")


@pytest.fixture(scope="module")
def real_sources(tmp_path_factory):
    if not REAL_FFMPEG:
        pytest.skip("FFmpeg/FFprobe absent on this host")
    directory = tmp_path_factory.mktemp("preview-synthetic")
    base = [
        "ffmpeg",
        "-hide_banner",
        "-v",
        "error",
        "-nostdin",
        "-threads",
        "1",
        "-filter_threads",
        "1",
        "-filter_complex_threads",
        "1",
    ]

    def generate(name, args):
        subprocess.run(
            [*base, *args, "-n", str(directory / name)],
            check=True,
            capture_output=True,
            timeout=30,
        )

    generate(
        "horizontal.mp4",
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=24:duration=12",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=48000:duration=12",
            "-c:v",
            "libx264",
            "-threads",
            "1",
            "-preset",
            "ultrafast",
            "-crf",
            "30",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-t",
            "12",
        ],
    )
    for name, dimensions, vf in [
        ("vertical.mp4", "180x320", "null"),
        ("square.mp4", "180x180", "null"),
        ("sar.mp4", "320x180", "setsar=2"),
        ("vfr.mp4", "320x180", "select='not(mod(n,3))+not(mod(n,5))'"),
    ]:
        generate(
            name,
            [
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=size={dimensions}:rate=24:duration=12",
                "-vf",
                vf,
                "-fps_mode",
                "vfr",
                "-an",
                "-c:v",
                "libx264",
                "-threads",
                "1",
                "-preset",
                "ultrafast",
                "-crf",
                "30",
                "-pix_fmt",
                "yuv420p",
            ],
        )
    generate(
        "rotated.mp4",
        [
            "-i",
            str(directory / "horizontal.mp4"),
            "-c",
            "copy",
            "-metadata:s:v:0",
            "rotate=90",
        ],
    )
    generate(
        "delayed-audio.mp4",
        [
            "-f",
            "lavfi",
            "-i",
            "color=black:size=64x48:rate=24:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=48000:duration=1.75",
            "-filter_complex",
            "[1:a]asetpts=PTS+0.25/TB[a]",
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-threads",
            "1",
            "-c:a",
            "aac",
            "-t",
            "2",
        ],
    )
    for name, duration, frequency in [("short.mp3", 1.2, 440), ("long.mp3", 12, 880)]:
        generate(
            name,
            [
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={frequency}:sample_rate=48000:duration={duration}",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "96k",
            ],
        )
    for path in directory.iterdir():
        path.chmod(0o400)
    return directory


def render_real(
    settings,
    tmp_path,
    real_sources,
    plan,
    source="horizontal.mp4",
    music=None,
    start=3000,
    duration=800,
):
    original = real_sources / source
    checksum, mode = hashlib.sha256(original.read_bytes()).hexdigest(), original.stat().st_mode
    stages = []
    with original.open("rb") as video:
        audio = (real_sources / music).open("rb") if music else None
        try:
            result = render_preview(
                settings,
                plan,
                source_fd=video.fileno(),
                source_video_stream_index=0,
                source_has_audio=source in {"horizontal.mp4", "rotated.mp4", "delayed-audio.mp4"},
                background_fd=audio.fileno() if audio else None,
                preview_start_ms=start,
                duration_ms=duration,
                workspace=tmp_path,
                runner=ProcessRunner(settings, Event()),
                set_stage=stages.append,
            )
        finally:
            if audio:
                audio.close()
    assert stages == ["encoding", "validating"]
    assert hashlib.sha256(original.read_bytes()).hexdigest() == checksum
    assert original.stat().st_mode == mode
    assert result.checksum_sha256 == hashlib.sha256(result.path.read_bytes()).hexdigest()
    assert result.byte_size == result.path.stat().st_size > 0
    assert result.metadata["decoded_frames"] > 0
    return result


@real_media
@pytest.mark.parametrize("preset", PROFILE_SIZES)
@pytest.mark.parametrize("fit", ["cover", "contain"])
def test_real_four_canvases_cover_contain_unicode_and_audio(
    settings, tmp_path, real_sources, preset, fit
):
    texts = [
        overlay(position=position, style=style, size=size)
        for position, style, size in [
            ("top", "white_outline", "small"),
            ("center", "blue_outline", "medium"),
            ("bottom", "white_plain", "large"),
        ]
    ]
    result = render_real(
        settings,
        tmp_path,
        real_sources,
        plan_v2(
            canvas={"preset": preset, "fit": fit, "focal_x": 0.25, "focal_y": 0.75},
            text_overlays=texts,
            audio={
                "background_audio_id": str(uuid4()),
                "source_volume": 0.8,
                "background_volume": 0.15,
            },
        ),
        music="long.mp3",
    )
    assert (result.metadata["width"], result.metadata["height"]) == PROFILE_SIZES[preset]
    assert result.metadata["has_audio"]


@real_media
@pytest.mark.parametrize(
    "source", ["vertical.mp4", "square.mp4", "sar.mp4", "rotated.mp4", "vfr.mp4"]
)
@pytest.mark.parametrize("fit", ["cover", "contain"])
def test_real_orientation_sar_vfr_and_silent_sources(settings, tmp_path, real_sources, source, fit):
    result = render_real(
        settings,
        tmp_path,
        real_sources,
        plan_v2(canvas={"preset": "9:16", "fit": fit, "focal_x": 1, "focal_y": 0}),
        source=source,
    )
    assert result.metadata["has_audio"] is (source == "rotated.mp4")
    assert result.metadata["sample_aspect_ratio"] == "1:1"


@real_media
@pytest.mark.parametrize("loop", [True, False])
def test_real_short_music_midtrim_loop_and_exhausted_silence(
    settings, tmp_path, real_sources, loop
):
    result = render_real(
        settings,
        tmp_path,
        real_sources,
        plan_v2(
            audio={
                "background_audio_id": str(uuid4()),
                "background_loop": loop,
                "fade_in_ms": 1500,
                "fade_out_ms": 2000,
            }
        ),
        source="vertical.mp4",
        music="short.mp3",
        start=8000,
    )
    assert result.metadata["audio_codec"] == "aac"


@real_media
@pytest.mark.parametrize("duration", [1, 8000])
def test_real_minimum_maximum_window_and_v1_compatibility(
    settings, tmp_path, real_sources, duration
):
    v2 = plan_v2().model_dump(exclude={"text_overlays", "audio"})
    v2["schema_version"] = 1
    result = render_real(
        settings, tmp_path, real_sources, EditSpecV1.model_validate(v2), duration=duration
    )
    assert abs(result.metadata["duration_ms"] - duration) <= 43


@real_media
@pytest.mark.parametrize("start", [3001, 10999, 11999])
def test_real_one_ms_window_between_frames(settings, tmp_path, real_sources, start):
    result = render_real(
        settings,
        tmp_path,
        real_sources,
        plan_v2(trim={"start_ms": 1000, "end_ms": 12000}),
        start=start,
        duration=1,
    )
    assert result.metadata["frame_count"] == 1


@real_media
def test_real_source_audio_delay_survives_seek_and_preroll(settings, tmp_path, real_sources):
    import array
    import math

    artifact = render_real(
        settings,
        tmp_path,
        real_sources,
        plan_v2(trim={"start_ms": 0, "end_ms": 2000}),
        source="delayed-audio.mp4",
        start=100,
        duration=500,
    )
    process = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            "-threads",
            "1",
            "-i",
            str(artifact.path),
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "f32le",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )
    samples = array.array("f", process.stdout)
    first = math.sqrt(sum(value * value for value in samples[:1600]) / 1600)
    last = math.sqrt(sum(value * value for value in samples[-1600:]) / 1600)
    assert first < 0.00001
    assert last > 0.05


@real_media
def test_real_artifact_is_never_overwritten(settings, tmp_path, real_sources):
    first = render_real(settings, tmp_path, real_sources, plan_v2())
    expected = first.path.read_bytes()
    with pytest.raises(PreviewProcessError, match="preview_encoding_failed"):
        render_real(settings, tmp_path, real_sources, plan_v2())
    assert first.path.read_bytes() == expected


@real_media
def test_real_complete_decode_rejects_corrupt_encoded_payload(settings, tmp_path, real_sources):
    artifact = render_real(settings, tmp_path, real_sources, plan_v2())
    damaged = tmp_path / "damaged.mp4"
    content = artifact.path.read_bytes()
    # Preserve the headers but truncate the media data; never touch an original.
    damaged.write_bytes(content[: len(content) // 2])
    with pytest.raises(PreviewProcessError, match="preview_validation_failed"):
        validate_artifact(
            settings,
            damaged,
            preset="9:16",
            duration_ms=800,
            expects_audio=True,
            runner=ProcessRunner(settings, Event()),
        )


@real_media
def test_real_file_symlink_is_rejected(settings, tmp_path, real_sources):
    symlink = tmp_path / "symlink.mp4"
    symlink.symlink_to(real_sources / "horizontal.mp4")
    with pytest.raises(PreviewProcessError, match="preview_storage_unavailable"):
        validate_artifact(
            settings,
            symlink,
            preset="9:16",
            duration_ms=800,
            expects_audio=True,
            runner=ProcessRunner(settings, Event()),
        )
