"""R05 final profile contracts and real synthetic composition, no database needed."""

import hashlib
import json
from threading import Event
from uuid import uuid4

import pytest
from test_preview_compositor import (  # noqa: F401
    CaptureRunner,
    font_path,
    good_metadata,
    overlay,
    plan_v2,
    real_media,
)
from test_preview_compositor import real_sources as real_sources

from app.integrations.ffmpeg.preview import (
    FINAL_PROFILE_SIZES,
    FINAL_PROFILES,
    PREVIEW_PROFILE,
    PROFILE_SIZES,
    framing_filter,
    render_final,
    validate_metadata,
)
from app.schemas.edit_spec import EditSpecV1
from app.worker.supervisor import (
    LeaseLost,
    PreviewProcessError,
    ProcessPolicy,
    ProcessRunner,
    ProcessStopped,
    RenderCanceled,
)


def final_metadata(preset="9:16", *, duration_ms=12000, audio=False):
    payload = good_metadata(audio=audio, preset=preset, duration_ms=duration_ms)
    video = payload["streams"][0]
    video.update(
        width=FINAL_PROFILE_SIZES[preset][0],
        height=FINAL_PROFILE_SIZES[preset][1],
        avg_frame_rate="30/1",
        nb_frames=str(round(duration_ms * 30 / 1000)),
    )
    return payload


@pytest.mark.parametrize("preset", FINAL_PROFILE_SIZES)
@pytest.mark.parametrize("profile", FINAL_PROFILES)
@pytest.mark.parametrize("audio", [False, True])
def test_final_profile_validates_complete_duration_over_eight_seconds(preset, profile, audio):
    controlled = validate_metadata(
        final_metadata(preset, audio=audio),
        preset,
        12000,
        audio,
        composition=FINAL_PROFILES[profile],
    )
    assert controlled["duration_ms"] == 12000
    assert controlled["fps_num"] == 30
    assert controlled["frame_count"] == 360
    assert controlled["audio_codec"] == ("aac" if audio else None)
    with pytest.raises(PreviewProcessError):
        validate_metadata(final_metadata(preset, audio=audio), preset, 12000, audio)


@pytest.mark.parametrize("preset", FINAL_PROFILE_SIZES)
@pytest.mark.parametrize("fit", ["cover", "contain"])
def test_framing_uses_same_engine_and_requested_output_dimensions(preset, fit):
    plan = plan_v2(canvas={"preset": preset, "fit": fit, "focal_x": 0.2, "focal_y": 0.8})
    preview = framing_filter(plan)
    final = framing_filter(plan, composition=FINAL_PROFILES["standard"])
    assert preview != final
    width, height = FINAL_PROFILE_SIZES[preset]
    assert (f"w={width}:h={height}" if fit == "cover" else f"pad={width}:{height}") in final
    assert PREVIEW_PROFILE.dimensions(preset) == PROFILE_SIZES[preset]
    assert "setsar=1" in final


@pytest.mark.parametrize("profile", FINAL_PROFILES)
def test_final_command_complete_trim_allowlisted_profiles_and_incremental_progress(
    settings, tmp_path, profile
):
    plan = plan_v2(trim={"start_ms": 2000, "end_ms": 14000})
    runner = CaptureRunner()
    stages = []
    with pytest.raises(PreviewProcessError, match="render_encoding_failed"):
        render_final(
            settings,
            plan,
            source_fd=11,
            source_video_stream_index=3,
            source_has_audio=True,
            background_fd=None,
            workspace=tmp_path,
            runner=runner,
            set_stage=stages.append,
            profile=profile,
            set_progress=lambda _: None,
        )
    argv, options = runner.calls[0]
    chosen = FINAL_PROFILES[profile]
    assert argv[argv.index("-ss") + 1] == "2.000"
    assert argv[argv.index("-t") + 1] == "12.000"
    assert argv[argv.index("-preset") + 1] == chosen.encoder_preset
    assert argv[argv.index("-crf") + 1] == str(chosen.crf)
    assert argv[argv.index("-b:a") + 1] == chosen.audio_bitrate
    assert argv[argv.index("-r") + 1] == "30"
    assert argv[argv.index("-pix_fmt") + 1] == "yuv420p"
    assert argv[argv.index("-progress") + 1] == "pipe:1"
    assert "fps=fps=30" in argv[argv.index("-filter_complex") + 1]
    assert str(tmp_path / "final.part") in argv
    assert options["stdout_limit"] == settings.render_progress_output_bytes
    assert callable(options["stdout_consumer"])
    assert stages == ["encoding"]


def test_final_never_accepts_untrusted_profile_or_truncates_to_media_limit(settings, tmp_path):
    runner = CaptureRunner()
    for profile, plan in [
        ("-preset arbitrary", plan_v2()),
        ("standard", plan_v2(trim={"start_ms": 0, "end_ms": 600001})),
    ]:
        with pytest.raises(PreviewProcessError, match="render_invalid_plan"):
            render_final(
                settings,
                plan,
                source_fd=11,
                source_video_stream_index=0,
                source_has_audio=False,
                background_fd=None,
                workspace=tmp_path,
                runner=runner,
                set_stage=lambda _: None,
                profile=profile,
                set_progress=lambda _: None,
            )
    assert runner.calls == []


@pytest.mark.parametrize("error_type", [LeaseLost, ProcessStopped, RenderCanceled])
def test_final_preserves_supervisor_control_flow_identity(settings, tmp_path, error_type):
    class Interrupted(CaptureRunner):
        def check(self):
            raise error_type()

    with pytest.raises(error_type):
        render_final(
            settings,
            plan_v2(),
            source_fd=11,
            source_video_stream_index=0,
            source_has_audio=False,
            background_fd=None,
            workspace=tmp_path,
            runner=Interrupted(),
            set_stage=lambda _: None,
            profile="standard",
            set_progress=lambda _: None,
        )


def render_real_final(
    settings,
    tmp_path,
    real_sources,
    plan,
    *,
    source="horizontal.mp4",
    music=None,
    profile="standard",
):
    originals = [real_sources / source] + ([real_sources / music] if music else [])
    before = [(hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mode) for p in originals]
    stages, progress = [], []
    with originals[0].open("rb") as source_file:
        audio = originals[1].open("rb") if music else None
        try:
            result = render_final(
                settings,
                plan,
                source_fd=source_file.fileno(),
                source_video_stream_index=0,
                source_has_audio=source in {"horizontal.mp4", "rotated.mp4"},
                background_fd=audio.fileno() if audio else None,
                workspace=tmp_path,
                runner=ProcessRunner(settings, Event(), policy=ProcessPolicy.for_render(settings)),
                set_stage=stages.append,
                profile=profile,
                set_progress=progress.append,
            )
        finally:
            if audio:
                audio.close()
    assert before == [
        (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mode) for p in originals
    ]
    assert stages == ["encoding", "validating"]
    assert progress == sorted(set(progress)) and progress[-1] > 0
    assert progress[-1] <= plan.trim.end_ms - plan.trim.start_ms
    assert result.metadata["fps_num"] == 30
    assert result.metadata["decoded_frames"] == result.metadata["frame_count"] > 0
    assert hashlib.sha256(result.path.read_bytes()).hexdigest() == result.checksum_sha256
    return result


@real_media
@pytest.mark.parametrize("preset", FINAL_PROFILE_SIZES)
@pytest.mark.parametrize("profile", FINAL_PROFILES)
def test_real_final_four_presets_both_profiles_and_unicode(
    settings, tmp_path, real_sources, preset, profile
):
    plan = plan_v2(
        trim={"start_ms": 2000, "end_ms": 2300},
        canvas={"preset": preset, "fit": "contain", "focal_x": 0.2, "focal_y": 0.8},
        text_overlays=[overlay(start_ms=2000, end_ms=2300)],
    )
    result = render_real_final(settings, tmp_path, real_sources, plan, profile=profile)
    assert (result.metadata["width"], result.metadata["height"]) == FINAL_PROFILE_SIZES[preset]
    assert abs(result.metadata["duration_ms"] - 300) <= 35


@real_media
def test_real_final_complete_v2_trim_over_eight_seconds_and_looped_audio(
    settings, tmp_path, real_sources
):
    plan = plan_v2(
        trim={"start_ms": 1000, "end_ms": 11000},
        text_overlays=[overlay()],
        audio={
            "background_audio_id": str(uuid4()),
            "background_loop": True,
            "source_volume": 0.7,
            "background_volume": 0.15,
            "fade_in_ms": 1000,
            "fade_out_ms": 1500,
        },
    )
    result = render_real_final(settings, tmp_path, real_sources, plan, music="short.mp3")
    assert result.metadata["duration_ms"] > 8000
    assert abs(result.metadata["duration_ms"] - 10000) <= 35
    assert result.metadata["frame_count"] == 300
    assert result.metadata["audio_codec"] == "aac"


@real_media
def test_real_final_v1_neutral_compatibility_and_no_source_audio(settings, tmp_path, real_sources):
    payload = plan_v2(trim={"start_ms": 1000, "end_ms": 10200}).model_dump(
        exclude={"text_overlays", "audio"}
    )
    payload["schema_version"] = 1
    plan = EditSpecV1.model_validate(payload)
    snapshot = plan.model_dump_json()
    result = render_real_final(settings, tmp_path, real_sources, plan, source="square.mp4")
    assert not result.metadata["has_audio"]
    assert result.metadata["duration_ms"] > 8000
    assert plan.model_dump_json() == snapshot


@real_media
@pytest.mark.parametrize(
    "source", ["vertical.mp4", "square.mp4", "sar.mp4", "rotated.mp4", "vfr.mp4"]
)
@pytest.mark.parametrize("fit", ["cover", "contain"])
def test_real_final_orientation_sar_vfr_focal(settings, tmp_path, real_sources, source, fit):
    plan = plan_v2(
        trim={"start_ms": 1000, "end_ms": 1300},
        canvas={"preset": "9:16", "fit": fit, "focal_x": 1.0, "focal_y": 0.0},
    )
    result = render_real_final(settings, tmp_path, real_sources, plan, source=source)
    assert result.metadata["sample_aspect_ratio"] == "1:1"
    assert result.metadata["width"] == 1080 and result.metadata["height"] == 1920


@real_media
@pytest.mark.parametrize("start", [2001, 11900])
def test_real_final_fractional_frame_boundary_and_final_source_frame(
    settings, tmp_path, real_sources, start
):
    result = render_real_final(
        settings,
        tmp_path,
        real_sources,
        plan_v2(trim={"start_ms": start, "end_ms": start + 100}),
    )
    assert abs(result.metadata["duration_ms"] - 100) <= 35
    assert 2 <= result.metadata["frame_count"] <= 4


@real_media
def test_real_final_original_and_background_gains_are_applied_without_normalization(
    settings, tmp_path, real_sources
):
    import array
    import math
    import subprocess

    levels = {"source": {}, "background": {}}
    peaks = {"source": {}, "background": {}}
    for mode in levels:
        volumes = (0.0, 0.5, 1.0, 2.0) if mode == "source" else (0.0, 0.5, 1.0)
        for volume in volumes:
            workspace = tmp_path / f"{mode}-{volume}"
            workspace.mkdir(mode=0o700)
            audio = (
                {"source_volume": volume}
                if mode == "source"
                else {"background_audio_id": str(uuid4()), "background_volume": volume}
            )
            artifact = render_real_final(
                settings,
                workspace,
                real_sources,
                plan_v2(
                    trim={"start_ms": 2000, "end_ms": 3000},
                    canvas={"preset": "1:1", "fit": "contain", "focal_x": 0.5, "focal_y": 0.5},
                    audio=audio,
                ),
                source="horizontal.mp4" if mode == "source" else "square.mp4",
                music=None if mode == "source" else "long.mp3",
            )
            decoded = subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-nostdin",
                    "-threads",
                    "1",
                    "-ss",
                    "0.2",
                    "-i",
                    str(artifact.path),
                    "-t",
                    "0.5",
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
                timeout=15,
            )
            samples = array.array("f", decoded.stdout)
            assert samples
            levels[mode][volume] = math.sqrt(sum(s * s for s in samples) / len(samples))
            peaks[mode][volume] = max(map(abs, samples))
            assert peaks[mode][volume] < 0.99
        assert levels[mode][0.0] < 0.00001
        assert 0.45 < levels[mode][0.5] / levels[mode][1.0] < 0.55
    assert 1.9 < levels["source"][2.0] / levels["source"][1.0] < 2.1
    print(
        "EDIVO_GAIN_METRICS="
        + json.dumps(
            {
                "rms": levels,
                "peak": peaks,
                "human_listening_performed": False,
                "source_frequency_hz": 1000,
                "background_frequency_hz": 880,
                "measurement": (
                    "Mono PCM float32, 16kHz, 0.2..0.7s of final MP4, no gain normalization"
                ),
                "limitation": (
                    "Synthetic signals below full scale; arbitrary high-gain mixes can clip"
                ),
            },
            sort_keys=True,
        )
    )


@real_media
def test_real_high_4x5_twelve_seconds_long_text_and_looped_audio_under_one_gib(
    settings, tmp_path, real_sources
):
    """Regression for the real R05 high-profile x264 malloc failure.

    A short profile smoke test did not exercise medium's buffered frames. This
    uses the complete12s trim, long text and looped music while preserving the
    production1GiB address-space budget; the render-only arena cap is essential.
    """
    import subprocess

    sources = tmp_path / "synthetic-inputs"
    sources.mkdir(mode=0o700)
    # Reuse the established synthetic horizontal/AAC fixture, extending it by
    # stream copy only. No existing fixture or application original is modified.
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            "-stream_loop",
            "1",
            "-i",
            str(real_sources / "horizontal.mp4"),
            "-t",
            "16",
            "-c",
            "copy",
            "-n",
            str(sources / "horizontal.mp4"),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=2",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "96k",
            "-n",
            str(sources / "short.mp3"),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    for source in sources.iterdir():
        source.chmod(0o400)
    before = {source.name: source.stat().st_mtime_ns for source in sources.iterdir()}
    plan = plan_v2(
        trim={"start_ms": 1000, "end_ms": 13000},
        canvas={"preset": "4:5", "fit": "cover", "focal_x": 0.25, "focal_y": 0.75},
        text_overlays=[
            overlay(
                text="Revisão posterior — export anterior preservado",
                start_ms=1000,
                end_ms=13000,
                position="bottom",
                style="blue_outline",
                size="medium",
            )
        ],
        audio={
            "source_volume": 0.7,
            "background_audio_id": str(uuid4()),
            "background_volume": 0.15,
            "background_loop": True,
            "fade_in_ms": 1000,
            "fade_out_ms": 1500,
        },
    )
    workspace = tmp_path / "high-export"
    workspace.mkdir(mode=0o700)
    fixed_budget = settings.model_copy(update={"render_memory_bytes": 1024**3, "render_threads": 2})
    assert ProcessPolicy.for_render(fixed_budget).limits.memory_bytes == 1073741824
    result = render_real_final(
        fixed_budget, workspace, sources, plan, music="short.mp3", profile="high"
    )
    assert (result.metadata["width"], result.metadata["height"]) == (1080, 1350)
    assert result.metadata["fps_num"] == 30 and result.metadata["fps_den"] == 1
    assert result.metadata["frame_count"] == result.metadata["decoded_frames"] == 360
    assert abs(result.metadata["duration_ms"] - 12000) <= 35
    assert result.metadata["audio_codec"] == "aac" and result.metadata["has_audio"]
    assert before == {source.name: source.stat().st_mtime_ns for source in sources.iterdir()}
