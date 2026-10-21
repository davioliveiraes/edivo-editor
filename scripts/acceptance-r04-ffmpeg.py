"""Run inside the backend runtime with synthetic fixtures and private /tmp only.

Example: python acceptance-r04-ffmpeg.py --fixtures /fixtures --output /evidence/ffmpeg-matrix.json
No database, HTTP, original mutation, publication or auditory inspection is performed.
"""

import argparse
import array
import hashlib
import json
import math
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from time import monotonic
from uuid import uuid4

from app.core.config import Settings
from app.integrations.ffmpeg.font_metrics import FONT_PATH, FONT_SHA256
from app.integrations.ffmpeg.preview import PROFILE_SIZES, render_preview
from app.schemas.edit_spec import EditSpecV2
from app.worker.supervisor import ProcessRunner


def rms(samples):
    return math.sqrt(sum(value * value for value in samples) / len(samples)) if samples else 0.0


def tone(samples, frequency, rate=16000):
    return abs(
        sum(
            value
            * complex(
                math.cos(2 * math.pi * frequency * index / rate),
                math.sin(2 * math.pi * frequency * index / rate),
            )
            for index, value in enumerate(samples)
        )
    ) / len(samples)


def plan(*, preset="9:16", fit="cover", texts=None, audio=None):
    return EditSpecV2.model_validate(
        {
            "schema_version": 2,
            "source_media_id": str(uuid4()),
            "trim": {"start_ms": 2000, "end_ms": 11000},
            "canvas": {"preset": preset, "fit": fit, "focal_x": 0.25, "focal_y": 0.75},
            "text_overlays": texts or [],
            "audio": audio or {},
        }
    )


def texts():
    return [
        {
            "id": str(uuid4()),
            "text": content,
            "start_ms": 2000,
            "end_ms": 11000,
            "position": position,
            "style": style,
            "size": size,
        }
        for content, position, style, size in [
            ("Olá: 'Edivo' \\ 100%\nTexto seguro", "top", "white_outline", "small"),
            ("Ação — prévia\nSeu próximo vídeo", "center", "blue_outline", "medium"),
            ("Coração e café\nEdição local", "bottom", "white_plain", "large"),
        ]
    ]


class Matrix:
    def __init__(self, settings, fixtures, evidence, work):
        self.settings, self.fixtures, self.evidence, self.work = settings, fixtures, evidence, work
        self.cases, self.checks, self.metrics = [], [], {}

    def check(self, name, condition, **details):
        self.checks.append({"name": name, "passed": bool(condition), **details})
        if not condition:
            raise AssertionError(name)

    def execute(self, args, *, output_limit=None):
        result = ProcessRunner(self.settings, Event()).run(args, output_limit=output_limit)
        if result.returncode:
            raise RuntimeError("synthetic_diagnostic_process_failed")
        return result.stdout

    def ffmpeg(self):
        return [
            self.settings.ffmpeg_bin,
            "-hide_banner",
            "-v",
            "error",
            "-nostdin",
            "-threads",
            "2",
            "-filter_threads",
            "2",
            "-filter_complex_threads",
            "2",
        ]

    def render(self, name, editing, source="horizontal.mp4", music=None, start=5000, duration=1000):
        workspace = self.work / name
        workspace.mkdir(mode=0o700)
        original = self.fixtures / source
        started, stages = monotonic(), []
        with original.open("rb") as video:
            sound = (self.fixtures / music).open("rb") if music else None
            try:
                result = render_preview(
                    self.settings,
                    editing,
                    source_fd=video.fileno(),
                    source_video_stream_index=0,
                    source_has_audio=source in {"horizontal.mp4", "rotated.mp4"},
                    background_fd=sound.fileno() if sound else None,
                    preview_start_ms=start,
                    duration_ms=duration,
                    workspace=workspace,
                    runner=ProcessRunner(self.settings, Event()),
                    set_stage=stages.append,
                )
            finally:
                if sound:
                    sound.close()
        self.cases.append(
            {
                "name": name,
                "source": source,
                "music": music,
                "source_window_ms": [start, start + duration],
                "schema_version": 2,
                "preset": editing.canvas.preset,
                "fit": editing.canvas.fit,
                "sha256": result.checksum_sha256,
                "bytes": result.byte_size,
                "metadata": result.metadata,
                "stages": stages,
                "seconds": round(monotonic() - started, 4),
            }
        )
        self.check(name + "_fully_validated", stages == ["encoding", "validating"])
        return result

    def pcm(self, path, *, start=0, duration=1):
        raw = self.execute(
            [
                *self.ffmpeg(),
                "-ss",
                str(start),
                "-i",
                str(path),
                "-map",
                "0:a:0",
                "-t",
                str(duration),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-f",
                "f32le",
                "pipe:1",
            ]
        )
        return array.array("f", raw)

    def frame(self, artifact, name, *, time=0.25):
        output = self.evidence / f"{name}.png"
        self.execute(
            [
                *self.ffmpeg(),
                "-ss",
                str(time),
                "-i",
                str(artifact.path),
                "-an",
                "-frames:v",
                "1",
                "-c:v",
                "png",
                "-threads",
                "1",
                "-n",
                str(output),
            ]
        )
        return output.name

    def pixels(self, artifact, *, time):
        width, height = artifact.metadata["width"] // 4, artifact.metadata["height"] // 4
        raw = self.execute(
            [
                *self.ffmpeg(),
                "-ss",
                str(time),
                "-i",
                str(artifact.path),
                "-an",
                "-vf",
                f"scale={width}:{height}",
                "-frames:v",
                "1",
                "-c:v",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-f",
                "rawvideo",
                "pipe:1",
            ]
        )
        self.check(f"rgb_frame_{time}_complete", len(raw) == width * height * 3)
        coordinates = []
        for index in range(width * height):
            r, g, b = raw[index * 3 : index * 3 + 3]
            if min(r, g, b) > 160 or (b > r + 65 and b > g + 40 and b > 145):
                coordinates.append((index % width, index // width))
        return coordinates, width, height

    def run(self):
        frames = []
        before = {
            path.name: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mode)
            for path in self.fixtures.iterdir()
            if path.is_file()
        }
        for preset in PROFILE_SIZES:
            for fit in ("cover", "contain"):
                name = f"canvas-{preset.replace(':', '-')}-{fit}"
                artifact = self.render(
                    name,
                    plan(
                        preset=preset,
                        fit=fit,
                        texts=texts(),
                        audio={
                            "background_audio_id": str(uuid4()),
                            "source_volume": 1,
                            "background_volume": 0.15,
                            "background_loop": True,
                        },
                    ),
                    music="music-long.mp3",
                )
                frames.append(self.frame(artifact, name))
        for source in ("silent-vertical.mp4", "square.mp4", "sar.mp4", "rotated.mp4", "vfr.mp4"):
            name = "orientation-" + source.removesuffix(".mp4")
            artifact = self.render(name, plan(fit="contain"), source=source)
            frames.append(self.frame(artifact, name))

        # The controlled 1 kHz tone stands in for source audio. These measurements
        # show gain/mix behavior; they are not a human voice intelligibility study.
        reference = self.render("source-volume-1", plan())
        reference_pcm = self.pcm(reference.path)
        source_rms = rms(reference_pcm)
        for volume in (0.25, 2):
            artifact = self.render(f"source-volume-{volume}", plan(audio={"source_volume": volume}))
            ratio = rms(self.pcm(artifact.path)) / source_rms
            self.metrics[f"source_gain_{volume}"] = ratio
            self.check(f"source_gain_{volume}", abs(ratio - volume) < 0.06)
        middle = self.render("fades-middle", plan(audio={"fade_in_ms": 3000, "fade_out_ms": 3000}))
        middle_pcm = self.pcm(middle.path)
        self.metrics["middle_fade_rms_ratio"] = rms(middle_pcm) / source_rms
        self.check(
            "middle_preview_does_not_restart_fades", abs(rms(middle_pcm) / source_rms - 1) < 0.04
        )
        beginning = self.render("fade-in", plan(audio={"fade_in_ms": 3000}), start=2000)
        ending = self.render("fade-out", plan(audio={"fade_out_ms": 3000}), start=10000)
        for name, artifact, rising in (("fade-in", beginning, True), ("fade-out", ending, False)):
            samples = self.pcm(artifact.path)
            first, last = rms(samples[:1600]), rms(samples[-1600:])
            self.metrics[name] = {"first_100ms_rms": first, "last_100ms_rms": last}
            self.check(name + "_full_trim_clock", last > first * 8 if rising else first > last * 8)

        music_plan = {
            "source_volume": 0,
            "background_audio_id": str(uuid4()),
            "background_volume": 0.5,
        }
        offset_artifact = self.render(
            "music-offset-middle", plan(audio=music_plan), music="music-long.mp3"
        )
        actual, expected = (
            rms(self.pcm(offset_artifact.path)),
            rms(self.pcm(self.fixtures / "music-long.mp3", start=3)) * 0.5,
        )
        self.metrics["music_offset"] = {
            "expected_offset_ms": 3000,
            "actual_rms": actual,
            "expected_rms": expected,
        }
        self.check("music_uses_offset_from_trim_zero", abs(actual / expected - 1) < 0.08)
        loop_samples = None
        for loop in (False, True):
            artifact = self.render(
                f"short-music-loop-{loop}",
                plan(
                    audio={
                        **music_plan,
                        "background_loop": loop,
                    }
                ),
                source="silent-vertical.mp4",
                music="music-short.mp3",
            )
            samples = self.pcm(artifact.path)
            self.metrics[f"short_music_loop_{loop}"] = rms(samples)
            self.check(
                f"short_music_loop_{loop}", rms(samples) > 0.01 if loop else rms(samples) < 0.00001
            )
            if loop:
                loop_samples = samples
        self.check("music_tone_really_decoded", tone(loop_samples, 660) > 0.01)

        mix = self.render(
            "voice-and-music",
            plan(
                audio={
                    "background_audio_id": str(uuid4()),
                    "background_volume": 0.15,
                }
            ),
            music="music-long.mp3",
        )
        mix_pcm = self.pcm(mix.path)
        voice, music = tone(mix_pcm, 1000), tone(mix_pcm, 880)
        peak = max(abs(value) for value in mix_pcm)
        self.metrics["default_mix"] = {
            "source_1000hz": voice,
            "music_880hz": music,
            "peak_abs": peak,
        }
        self.check("default_mix_keeps_source_tone_dominant", voice > music * 2.8)
        self.check("synthetic_mix_no_gross_clipping", peak < 0.95)

        # A flat synthetic source makes burned-in pixels and timing measurable.
        flat = self.work / "flat.mp4"
        self.execute(
            [
                *self.ffmpeg(),
                "-f",
                "lavfi",
                "-i",
                "color=c=0x101410:s=640x360:r=24:d=12",
                "-an",
                "-c:v",
                "libx264",
                "-threads",
                "2",
                "-pix_fmt",
                "yuv420p",
                "-n",
                str(flat),
            ]
        )
        original_fixtures = self.fixtures
        self.fixtures = self.work
        try:
            safety = self.render("text-safe-area", plan(texts=texts()), source="flat.mp4")
            coordinates, width, height = self.pixels(safety, time=0.25)
            xs, ys = zip(*coordinates, strict=True)
            self.metrics["text_safe_area"] = {
                "scaled_resolution": [width, height],
                "bbox": [min(xs), min(ys), max(xs), max(ys)],
            }
            self.check(
                "burned_text_within_safe_area",
                min(xs) >= width * 0.07 - 1
                and max(xs) <= width * 0.93 + 1
                and min(ys) >= height * 0.07 - 1
                and max(ys) <= height * 0.93 + 1,
            )
            frames.append(self.frame(safety, "text-safe-area"))
            timed = texts()[:1]
            timed[0].update(start_ms=5000, end_ms=6000)
            timing = self.render(
                "text-time-window", plan(texts=timed), source="flat.mp4", start=4500, duration=2000
            )
            counts = [len(self.pixels(timing, time=time)[0]) for time in (0.1, 0.8, 1.8)]
            self.metrics["text_absolute_timing_pixels"] = counts
            self.check(
                "text_appears_only_during_absolute_interval",
                counts[0] == 0 and counts[1] > 20 and counts[2] == 0,
            )
        finally:
            self.fixtures = original_fixtures
        after = {
            path.name: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mode)
            for path in self.fixtures.iterdir()
            if path.is_file()
        }
        self.check("all_fixture_hashes_and_modes_unchanged", before == after, count=len(before))
        return frames


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    started = monotonic()
    report = {
        "status": "running",
        "generated_at": datetime.now(UTC).isoformat(),
        "synthetic_only": True,
        "database_or_http_used": False,
        "manual_auditory_inspection_performed": False,
        "font_sha256": FONT_SHA256,
        "limitations": [
            "Tone/RMS analysis is objective signal validation, not subjective human listening.",
            "CSS and FreeType rasterization can differ by pixels; "
            "unsupported font glyphs fail explicitly.",
            "CFR windows quantize within one frame + 1ms; absolute output maximum is8.001s.",
        ],
    }
    with tempfile.TemporaryDirectory(prefix="edivo-r04-ffmpeg-matrix-") as temp:
        matrix = Matrix(Settings(_env_file=None), args.fixtures, args.output.parent, Path(temp))
        try:
            report["frames"] = matrix.run()
            report["font_runtime_sha256"] = hashlib.sha256(FONT_PATH.read_bytes()).hexdigest()
            report["status"] = "passed"
        except Exception as error:
            report["status"] = "failed"
            report["failure"] = (
                str(error) if isinstance(error, AssertionError) else type(error).__name__
            )
        report.update(
            {
                "cases": matrix.cases,
                "checks": matrix.checks,
                "audio_and_text_metrics": matrix.metrics,
                "elapsed_seconds": round(monotonic() - started, 3),
            }
        )
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": report["status"],
                "cases": len(report["cases"]),
                "checks": len(report["checks"]),
                "seconds": report["elapsed_seconds"],
            }
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
