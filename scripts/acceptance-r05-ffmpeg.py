"""Real, isolated R05 FFmpeg matrix; synthetic files only, no DB/publication.

Run in backend image with current app mounted read-only. Fixtures and derived
QA artifacts live in explicit private paths. Existing files are never replaced.
Audio metrics are automated checks, not a claim of human auditory inspection.
"""

import argparse
import array
import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from time import monotonic
from uuid import uuid4

from app.core.config import Settings
from app.integrations.ffmpeg.font_metrics import FONT_PATH, FONT_SHA256
from app.integrations.ffmpeg.preview import (
    FINAL_PROFILE_SIZES,
    render_final,
    render_preview,
)
from app.schemas.edit_spec import EditSpecV1, EditSpecV2
from app.worker.supervisor import ProcessPolicy, ProcessRunner


def rms(samples):
    return (
        math.sqrt(sum(value * value for value in samples) / len(samples))
        if samples
        else 0.0
    )


def sha(path):
    checksum = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            checksum.update(chunk)
    return checksum.hexdigest()


def plan(
    *, preset="9:16", fit="cover", duration=1000, texts=None, audio=None, version=2
):
    payload = {
        "schema_version": version,
        "source_media_id": str(uuid4()),
        "trim": {"start_ms": 2000, "end_ms": 2000 + duration},
        "canvas": {"preset": preset, "fit": fit, "focal_x": 0.25, "focal_y": 0.75},
    }
    if version == 2:
        payload.update(text_overlays=texts or [], audio=audio or {})
    return (EditSpecV2 if version == 2 else EditSpecV1).model_validate(payload)


class Matrix:
    def __init__(self, settings, fixtures, evidence, artifacts):
        self.settings, self.fixtures = settings, fixtures
        self.evidence, self.artifacts = evidence, artifacts
        self.cases, self.checks, self.metrics, self.frames = [], [], {}, []

    def check(self, name, passed, **details):
        self.checks.append({"name": name, "passed": bool(passed), **details})
        if not passed:
            raise AssertionError(name)

    def execute(self, argv, *, output_limit=None):
        result = ProcessRunner(
            self.settings, Event(), policy=ProcessPolicy.for_render(self.settings)
        ).run(argv, output_limit=output_limit)
        if result.returncode:
            raise RuntimeError("synthetic_diagnostic_process_failed")
        return result.stdout

    def ffmpeg(self):
        return [
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

    def generate(self):
        self.fixtures.mkdir(mode=0o700, parents=True, exist_ok=True)

        def encode(name, argv):
            destination = self.fixtures / name
            if destination.exists():
                raise RuntimeError("existing_synthetic_fixture_not_overwritten")
            self.execute([*self.ffmpeg(), *argv, "-n", str(destination)])
            destination.chmod(0o400)

        for name, size, vf in [
            ("horizontal.mp4", "320x180", "null"),
            ("vertical.mp4", "180x320", "null"),
            ("square.mp4", "180x180", "null"),
            ("sar.mp4", "320x180", "setsar=2"),
            ("vfr.mp4", "320x180", "select='not(mod(n,3))+not(mod(n,5))'"),
            ("black.mp4", "320x180", "null"),
        ]:
            generator = "color=black" if name == "black.mp4" else "testsrc2"
            source = (
                f"{generator}:size={size}:rate=24:duration=16"
                if generator == "color=black"
                else (f"{generator}=size={size}:rate=24:duration=16")
            )
            args = ["-f", "lavfi", "-i", source]
            audio = name == "horizontal.mp4"
            if audio:
                args += [
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=1000:sample_rate=48000:duration=16",
                ]
            args += [
                "-vf",
                vf,
                "-fps_mode",
                "vfr",
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
            ]
            args += ["-c:a", "aac"] if audio else ["-an"]
            encode(name, args)
        encode(
            "rotated.mp4",
            [
                "-i",
                str(self.fixtures / "horizontal.mp4"),
                "-c",
                "copy",
                "-metadata:s:v:0",
                "rotate=90",
            ],
        )
        for name, duration, frequency in [("short.mp3", 2, 440), ("long.mp3", 20, 880)]:
            encode(
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

    def units(self):
        raw = self.execute(
            [
                *self.ffmpeg(),
                "-f",
                "lavfi",
                "-i",
                "color=black:size=64x48:rate=30:duration=1",
                "-map",
                "0:v:0",
                "-f",
                "null",
                "-",
                "-progress",
                "pipe:1",
            ]
        )
        fields = dict(
            line.split("=", 1)
            for line in raw.decode("ascii").splitlines()
            if "=" in line
        )
        microseconds = int(fields["out_time_us"])
        legacy = int(fields["out_time_ms"])
        self.check(
            "ffmpeg_real_progress_alias_is_microseconds",
            microseconds == legacy == 1000000,
        )
        document = {
            "created_at": datetime.now(UTC).isoformat(),
            "ffmpeg": self.execute(["ffmpeg", "-version"]).decode().splitlines()[0],
            "known_duration_seconds": 1,
            "frame_count": int(fields["frame"]),
            "out_time_us": microseconds,
            "out_time_ms": legacy,
            "out_time": fields["out_time"],
            "progress": fields["progress"],
            "units_verified": "Both out_time_us and historical out_time_ms are microseconds",
            "raw_progress": raw.decode("ascii"),
        }
        (self.evidence / "progress-units.json").write_text(
            json.dumps(document, indent=2) + "\n"
        )
        return document

    def render(
        self,
        name,
        editing,
        *,
        source="horizontal.mp4",
        music=None,
        profile="standard",
        preview=False,
    ):
        workspace = self.artifacts / name
        workspace.mkdir(mode=0o700)
        stages, progress = [], []
        started = monotonic()
        with (self.fixtures / source).open("rb") as video:
            sound = (self.fixtures / music).open("rb") if music else None
            try:
                kwargs = {
                    "source_fd": video.fileno(),
                    "source_video_stream_index": 0,
                    "source_has_audio": source in {"horizontal.mp4", "rotated.mp4"},
                    "background_fd": sound.fileno() if sound else None,
                    "workspace": workspace,
                    "set_stage": stages.append,
                }
                if preview:
                    result = render_preview(
                        self.settings,
                        editing,
                        **kwargs,
                        preview_start_ms=editing.trim.start_ms,
                        duration_ms=1000,
                        runner=ProcessRunner(self.settings, Event()),
                    )
                else:
                    result = render_final(
                        self.settings,
                        editing,
                        **kwargs,
                        profile=profile,
                        set_progress=progress.append,
                        runner=ProcessRunner(
                            self.settings,
                            Event(),
                            policy=ProcessPolicy.for_render(self.settings),
                        ),
                    )
            finally:
                if sound:
                    sound.close()
        self.check(name + "_fully_decoded", stages == ["encoding", "validating"])
        if not preview:
            self.check(
                name + "_measured_progress",
                progress and progress == sorted(set(progress)),
            )
        self.check(name + "_checksum", sha(result.path) == result.checksum_sha256)
        self.cases.append(
            {
                "name": name,
                "source": source,
                "music": music,
                "profile": profile,
                "schema_version": editing.schema_version,
                "saved_spec": editing.model_dump(mode="json"),
                "path": str(result.path),
                "metadata": result.metadata,
                "sha256": result.checksum_sha256,
                "bytes": result.byte_size,
                "stages": stages,
                "encoded_ms_observations": progress,
                "elapsed_seconds": round(monotonic() - started, 3),
            }
        )
        return result

    def pcm(self, artifact, start, duration):
        raw = self.execute(
            [
                *self.ffmpeg(),
                "-ss",
                str(start),
                "-i",
                str(artifact.path),
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
            ],
            output_limit=4 * 1024 * 1024,
        )
        return array.array("f", raw)

    def frame(self, artifact, name, at=1):
        destination = self.evidence / f"{name}.png"
        self.execute(
            [
                *self.ffmpeg(),
                "-ss",
                str(at),
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
                str(destination),
            ]
        )
        self.frames.append({"filename": destination.name, "sha256": sha(destination)})

    def run(self):
        self.units()
        self.generate()
        before = {
            p.name: {
                "sha256": sha(p),
                "mode": oct(p.stat().st_mode & 0o777),
                "mtime_ns": p.stat().st_mtime_ns,
            }
            for p in self.fixtures.iterdir()
        }
        for preset in FINAL_PROFILE_SIZES:
            for profile in ("standard", "high"):
                self.render(
                    f"{preset.replace(':', 'x')}-{profile}",
                    plan(preset=preset, fit="contain"),
                    profile=profile,
                )
        texts = [
            {
                "id": str(uuid4()),
                "text": text,
                "start_ms": 2000,
                "end_ms": 14000,
                "position": position,
                "style": style,
                "size": size,
            }
            for text, position, style, size in [
                ("Olá: 'Edivo' \\ 100%\nTexto seguro", "top", "white_outline", "small"),
                (
                    "Ação — composição\nLocal e privada",
                    "center",
                    "blue_outline",
                    "medium",
                ),
                ("Coração e café\nR05 completa", "bottom", "white_plain", "large"),
            ]
        ]
        decisive = self.render(
            "complete-12s-text-music",
            plan(
                duration=12000,
                texts=texts,
                audio={
                    "background_audio_id": str(uuid4()),
                    "background_loop": True,
                    "background_volume": 0.3,
                    "source_volume": 0.7,
                    "fade_in_ms": 1500,
                    "fade_out_ms": 2000,
                },
            ),
            source="black.mp4",
            music="short.mp3",
        )
        self.check(
            "complete_trim_over_eight_seconds", decisive.metadata["duration_ms"] > 8000
        )
        self.check(
            "complete_trim_exact_360_frames", decisive.metadata["frame_count"] == 360
        )
        self.frame(decisive, "complete-12s-text-music")
        signal = self.pcm(decisive, 0, 12)
        peak = max(map(abs, signal))
        steady, early, late = (
            rms(signal[32000:48000]),
            rms(signal[:3200]),
            rms(signal[-3200:]),
        )
        self.metrics["complete_looped_music"] = {
            "sample_peak": peak,
            "steady_rms": steady,
            "first_200ms_rms": early,
            "last_200ms_rms": late,
            "human_listening_performed": False,
        }
        self.check("looped_music_survives_short_mp3", rms(signal[128000:144000]) > 0.01)
        self.check(
            "fades_relative_to_complete_trim", early < steady / 3 and late < steady / 3
        )
        self.check("representative_audio_no_clipped_samples", peak < 0.99)
        raw = self.execute(
            [
                *self.ffmpeg(),
                "-ss",
                "1",
                "-i",
                str(decisive.path),
                "-an",
                "-vf",
                "scale=270:480",
                "-frames:v",
                "1",
                "-c:v",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-f",
                "rawvideo",
                "pipe:1",
            ],
            output_limit=512 * 1024,
        )
        bands = [0, 0, 0]
        for index in range(270 * 480):
            r, g, b = raw[index * 3 : index * 3 + 3]
            if min(r, g, b) > 160 or (b > r + 65 and b > g + 40 and b > 145):
                bands[min(2, (index // 270) * 3 // 480)] += 1
        self.metrics["text_pixels_per_vertical_third"] = bands
        self.check(
            "three_text_positions_visible_in_real_encoded_frame",
            all(n > 100 for n in bands),
        )
        nonloop = self.render(
            "music-no-loop",
            plan(
                duration=12000,
                audio={
                    "background_audio_id": str(uuid4()),
                    "background_loop": False,
                    "background_volume": 0.3,
                },
            ),
            source="black.mp4",
            music="short.mp3",
        )
        self.check(
            "nonlooped_music_ends_into_silence", rms(self.pcm(nonloop, 8, 1)) < 0.00001
        )
        self.render(
            "v1-complete-no-audio", plan(duration=12000, version=1), source="square.mp4"
        )
        self.render(
            "long-music",
            plan(audio={"background_audio_id": str(uuid4())}),
            music="long.mp3",
        )
        for source in (
            "vertical.mp4",
            "square.mp4",
            "sar.mp4",
            "vfr.mp4",
            "rotated.mp4",
        ):
            for fit in ("cover", "contain"):
                self.render(
                    source.removesuffix(".mp4") + "-" + fit,
                    plan(fit=fit, duration=400),
                    source=source,
                )
        self.render("preview-regression", plan(), preview=True)
        after = {
            p.name: {
                "sha256": sha(p),
                "mode": oct(p.stat().st_mode & 0o777),
                "mtime_ns": p.stat().st_mtime_ns,
            }
            for p in self.fixtures.iterdir()
        }
        self.check("all_synthetic_originals_sha_mode_mtime_unchanged", before == after)
        return before


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.artifacts.mkdir(mode=0o700, parents=True, exist_ok=True)
    started = monotonic()
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "database_or_http_used": False,
        "human_auditory_inspection_performed": False,
        "manual_visual_inspection_performed": False,
        "font_sha256": FONT_SHA256,
        "fixtures_directory": str(args.fixtures),
        "artifacts_directory": str(args.artifacts),
        "limitations": [
            "Automated RMS/peak and pixels do not imply human listening.",
            "CFR30 quantization tolerance is one frame +1ms.",
            "QA artifacts are synthetic and not application exports.",
        ],
    }
    matrix = Matrix(
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://qa:qa-only@127.0.0.1/unused",
        ),
        args.fixtures,
        args.output.parent,
        args.artifacts,
    )
    try:
        report["fixtures"] = matrix.run()
        report["font_runtime_sha256"] = sha(FONT_PATH)
        report["status"] = "passed"
    except (RuntimeError, ValueError, OSError, AssertionError) as error:
        report["status"] = "failed"
        report["failure"] = (
            str(error) if isinstance(error, AssertionError) else type(error).__name__
        )
        # Keep a code-only diagnostic without paths, source text or command lines.
        report["error_code"] = getattr(error, "code", None)
    report.update(
        cases=matrix.cases,
        checks=matrix.checks,
        audio_and_text_metrics=matrix.metrics,
        frames=matrix.frames,
        elapsed_seconds=round(monotonic() - started, 3),
    )
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": report["status"],
                "cases": len(matrix.cases),
                "checks": len(matrix.checks),
                "seconds": report["elapsed_seconds"],
            }
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
