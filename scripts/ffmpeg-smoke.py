#!/usr/bin/env python3
"""Diagnóstico sintético R01; não recebe mídia, texto ou flags do usuário."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def run(arguments: list[str]) -> str:
    result = subprocess.run(
        arguments,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return result.stdout


def main() -> int:
    ffmpeg = os.environ.get("EDIVO_FFMPEG_BIN", "ffmpeg")
    ffprobe = os.environ.get("EDIVO_FFPROBE_BIN", "ffprobe")
    try:
        ffmpeg_version = run([ffmpeg, "-version"]).splitlines()[0].split()[2]
        ffprobe_version = run([ffprobe, "-version"]).splitlines()[0].split()[2]
        with tempfile.TemporaryDirectory(prefix="edivo-r01-smoke-") as directory:
            output = Path(directory) / "synthetic.mp4"
            run(
                [
                    ffmpeg,
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=0x17211b:s=320x180:r=24",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:sample_rate=48000",
                    "-t",
                    "1",
                    "-vf",
                    "scale=320:180,crop=300:160,pad=320:180:10:10,"
                    "drawtext=text=Edivo:fontcolor=white:fontsize=24:x=24:y=24",
                    "-c:v",
                    "libx264",
                    "-threads",
                    "1",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-movflags",
                    "+faststart",
                    str(output),
                ]
            )
            inspection = json.loads(
                run(
                    [
                        ffprobe,
                        "-v",
                        "error",
                        "-show_entries",
                        "stream=codec_type,codec_name,width,height:format=duration",
                        "-of",
                        "json",
                        str(output),
                    ]
                )
            )
            video = next(s for s in inspection["streams"] if s["codec_type"] == "video")
            audio = next(s for s in inspection["streams"] if s["codec_type"] == "audio")
            duration = float(inspection["format"]["duration"])
            if (
                video["codec_name"] != "h264"
                or audio["codec_name"] != "aac"
                or (video["width"], video["height"]) != (320, 180)
                or not 0.9 <= duration <= 1.2
            ):
                raise ValueError("unexpected_probe_result")
            run(
                [
                    ffmpeg,
                    "-nostdin",
                    "-v",
                    "error",
                    "-xerror",
                    "-i",
                    str(output),
                    "-f",
                    "null",
                    "-",
                ]
            )
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "ffmpeg": ffmpeg_version,
                        "ffprobe": ffprobe_version,
                        "video_codec": video["codec_name"],
                        "audio_codec": audio["codec_name"],
                        "resolution": [video["width"], video["height"]],
                        "duration_seconds": duration,
                        "filters_exercised": ["scale", "crop", "pad", "drawtext"],
                        "decode": "ok",
                    }
                )
            )
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError, StopIteration):
        print(json.dumps({"status": "unavailable", "code": "synthetic_smoke_failed"}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
