"""Compose and fully validate a short preview from a frozen, typed editing plan.

No database access, publication or shell execution belongs here. Inputs are
already-authorized pinned descriptors. Only this module's allowlisted filters
and profiles become executable options; text content remains UTF-8 file data.
"""

import hashlib
import json
import math
import os
import stat
import struct
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.integrations.ffmpeg.font_metrics import FONT_PATH, FontLayoutError, FontMetrics
from app.schemas.edit_spec import AudioSpec, EditSpecV1, EditSpecV2
from app.worker.supervisor import PreviewProcessError, ProcessRunner

PROFILE_SIZES = {"9:16": (540, 960), "1:1": (540, 540), "4:5": (544, 680), "16:9": (960, 540)}
FPS = 24
# CFR output quantizes a very short window to at least one frame. MP4 duration
# also rounds to milliseconds: e.g. a 1 ms request is one 41.667 ms video frame.
DURATION_TOLERANCE_SECONDS = 1 / FPS + 0.001
_MAX_ALLOC = "67108864"


@dataclass(frozen=True)
class PreviewArtifact:
    path: Path
    byte_size: int
    checksum_sha256: str
    metadata: dict[str, Any]


def _seconds(milliseconds: int) -> str:
    return f"{milliseconds // 1000}.{milliseconds % 1000:03d}"


def _number(value: float) -> str:
    return format(value, ".12g")


def _common(settings: Settings) -> list[str]:
    return [
        settings.ffmpeg_bin,
        "-hide_banner",
        "-v",
        "error",
        "-nostdin",
        "-xerror",
        "-max_alloc",
        _MAX_ALLOC,
        "-threads",
        str(settings.preview_threads),
        "-filter_threads",
        str(settings.preview_threads),
        "-filter_complex_threads",
        str(settings.preview_threads),
    ]


def _input_options(settings: Settings, kind: str) -> list[str]:
    options = [
        "-threads",
        str(settings.preview_threads),
        "-err_detect",
        "explode",
        "-protocol_whitelist",
        "file,pipe",
        "-format_whitelist",
        kind,
        "-probesize",
        "5242880",
        "-analyzeduration",
        "5000000",
    ]
    if kind == "mov":
        options += ["-enable_drefs", "0", "-use_absolute_path", "0"]
    return [*options, "-f", kind]


def framing_filter(plan: EditSpecV1 | EditSpecV2) -> str:
    width, height = PROFILE_SIZES[plan.canvas.preset]
    ratio = f"{width}/{height}"
    # Scale expressions use display aspect ratio, after FFmpeg's autorotation.
    # force_original_aspect_ratio alone would use coded dimensions for SAR != 1.
    if plan.canvas.fit == "cover":
        scale = (
            f"scale=w='if(gte(dar,{ratio}),ceil({height}*dar/2)*2,{width})':"
            f"h='if(gte(dar,{ratio}),{height},ceil({width}/dar/2)*2)':flags=bicubic"
        )
        framing = (
            f"crop=w={width}:h={height}:x='(iw-ow)*{_number(plan.canvas.focal_x)}':"
            f"y='(ih-oh)*{_number(plan.canvas.focal_y)}':exact=1"
        )
    else:
        scale = (
            f"scale=w='if(gte(dar,{ratio}),{width},max(2,trunc({height}*dar/2)*2))':"
            f"h='if(gte(dar,{ratio}),max(2,trunc({width}/dar/2)*2),{height})':flags=bicubic"
        )
        framing = f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x101410"
    return f"setsar='if(gt(sar,0),sar,1)',{scale},setsar=1,{framing}"


def _write_text(workspace: Path, filename: str, content: str) -> None:
    directory_fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(
            filename,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=directory_fd,
        )
        with os.fdopen(fd, "wb") as target:
            target.write(content.encode("utf-8"))
    finally:
        os.close(directory_fd)


def _text_filters(
    plan: EditSpecV1 | EditSpecV2,
    preview_start_ms: int,
    duration_ms: int,
    workspace: Path,
    runner: ProcessRunner,
) -> list[str]:
    if not isinstance(plan, EditSpecV2) or not plan.text_overlays:
        return []
    width, height = PROFILE_SIZES[plan.canvas.preset]
    filters = []
    with FontMetrics() as font:
        for index, overlay in enumerate(plan.text_overlays):
            runner.check()
            start = max(0, overlay.start_ms - preview_start_ms)
            end = min(duration_ms, overlay.end_ms - preview_start_ms)
            if start >= end:
                continue
            layout = font.layout(
                overlay.text,
                overlay.size,
                overlay.position,
                overlay.style != "white_plain",
                width,
                height,
            )
            color = "0x3b82f6" if overlay.style == "blue_outline" else "white"
            for line_index, line in enumerate(overlay.text.split("\n")):
                filename = f"overlay-{index}-line-{line_index}.txt"
                _write_text(workspace, filename, line)
                # One filter per line centers unequal line lengths like CSS.
                baseline = layout.block_top + line_index * layout.line_height + layout.baseline
                y = (
                    f"max({height * 0.07 + layout.border_width:.6f},"
                    f"min({baseline:.6f}-ascent,"
                    f"{height * 0.93 - layout.border_width:.6f}-text_h))"
                )
                filters.append(
                    f"drawtext=fontfile={FONT_PATH}:textfile={filename}:expansion=none:"
                    f"fontsize={layout.font_size}:fontcolor={color}:"
                    f"borderw={layout.border_width}:bordercolor=0x101410:"
                    f"x=(w-text_w)/2:y='{y}':"
                    f"enable='gte(t,{_seconds(start)})*lt(t,{_seconds(end)})'"
                )
    return filters


def _audio_graph(
    plan: EditSpecV1 | EditSpecV2,
    source_has_audio: bool,
    music: bool,
    preview_start_ms: int,
    duration_ms: int,
) -> list[str]:
    audio = plan.audio if isinstance(plan, EditSpecV2) else AudioSpec()
    seconds = _seconds(duration_ms)
    offset = _seconds(preview_start_ms - plan.trim.start_ms)
    # The silent first input defines an exact window even after a nonlooped MP3
    # has ended, or if the source audio starts late or finishes before its video.
    filters = [f"anullsrc=r=48000:cl=stereo,atrim=duration={seconds},asetpts=N/SR/TB[silence]"]
    labels = ["[silence]"]
    if source_has_audio:
        filters.append(
            "[0:a:0]aresample=48000:first_pts=0,"
            "aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"atrim=duration={seconds},asetpts=N/SR/TB,"
            f"volume={_number(audio.source_volume)}[source_audio]"
        )
        labels.append("[source_audio]")
    if music:
        filters.append(
            "[1:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"asetpts=N/SR/TB,atrim=start={offset}:duration={seconds},"
            f"asetpts=PTS-STARTPTS,volume={_number(audio.background_volume)}[music]"
        )
        labels.append("[music]")
    mix = (
        f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:"
        f"dropout_transition=0:normalize=0,asetpts=PTS-STARTPTS+{offset}/TB"
    )
    if audio.fade_in_ms:
        mix += f",afade=t=in:st=0:d={_seconds(audio.fade_in_ms)}"
    if audio.fade_out_ms:
        fade_start = plan.trim.end_ms - plan.trim.start_ms - audio.fade_out_ms
        mix += f",afade=t=out:st={_seconds(fade_start)}:d={_seconds(audio.fade_out_ms)}"
    filters.append(f"{mix},asetpts=PTS-STARTPTS[audio_out]")
    return filters


def _finite_duration(value: object) -> float:
    try:
        number = float(value)
        if not math.isfinite(number) or number <= 0:
            raise ValueError
        return number
    except (ValueError, TypeError, OverflowError):
        raise PreviewProcessError("preview_validation_failed") from None


def validate_metadata(
    payload: object, preset: str, duration_ms: int, expects_audio: bool
) -> dict[str, Any]:
    """Return only controlled fields; never publish tags, paths or raw probe JSON."""
    try:
        if not isinstance(payload, dict) or not isinstance(payload.get("streams"), list):
            raise ValueError
        streams, info = payload["streams"], payload["format"]
        videos = [stream for stream in streams if stream.get("codec_type") == "video"]
        audios = [stream for stream in streams if stream.get("codec_type") == "audio"]
        if len(videos) != 1 or len(audios) != int(expects_audio):
            raise ValueError
        if len(streams) != 1 + int(expects_audio) or "mp4" not in info["format_name"].split(","):
            raise ValueError
        video = videos[0]
        width, height = PROFILE_SIZES[preset]
        start_times = (float(info.get("start_time", 0)), float(video.get("start_time", 0)))
        if (
            video.get("codec_name") != "h264"
            or video.get("pix_fmt") != "yuv420p"
            or video.get("width") != width
            or video.get("height") != height
            or Fraction(video["avg_frame_rate"]) != FPS
            or video.get("sample_aspect_ratio") != "1:1"
            or int(video["nb_frames"]) < 1
        ):
            raise ValueError
        if any(float(item.get("rotation", 0)) % 360 for item in video.get("side_data_list", [])):
            raise ValueError
        requested = duration_ms / 1000
        duration, video_duration = (
            _finite_duration(info["duration"]),
            _finite_duration(video["duration"]),
        )
        if (
            abs(duration - requested) > DURATION_TOLERANCE_SECONDS
            or abs(video_duration - requested) > DURATION_TOLERANCE_SECONDS
            or duration > 8.001
            or any(not math.isfinite(value) or abs(value) > 0.001 for value in start_times)
        ):
            raise ValueError
        if audios and (
            audios[0].get("codec_name") != "aac"
            or audios[0].get("sample_rate") != "48000"
            or audios[0].get("channels") != 2
            or abs(_finite_duration(audios[0]["duration"]) - requested) > DURATION_TOLERANCE_SECONDS
        ):
            raise ValueError
        return {
            "duration_ms": round(duration * 1000),
            "requested_duration_ms": duration_ms,
            "width": width,
            "height": height,
            "fps_num": FPS,
            "fps_den": 1,
            "video_codec": "h264",
            "pixel_format": "yuv420p",
            "sample_aspect_ratio": "1:1",
            "has_audio": expects_audio,
            "audio_codec": "aac" if expects_audio else None,
            "frame_count": int(video["nb_frames"]),
        }
    except (KeyError, ValueError, TypeError, AttributeError, OverflowError, ZeroDivisionError):
        raise PreviewProcessError("preview_validation_failed") from None


def _faststart(file: Any, size: int) -> bool:
    offset, found_moov = 0, False
    while offset < size:
        file.seek(offset)
        header = file.read(8)
        if len(header) != 8:
            return False
        length, kind = struct.unpack(">I4s", header)
        header_size = 8
        if length == 1:
            large = file.read(8)
            if len(large) != 8:
                return False
            length, header_size = struct.unpack(">Q", large)[0], 16
        if length < header_size or offset + length > size:
            return False
        if kind == b"moov":
            found_moov = True
        if kind == b"mdat":
            return found_moov
        offset += length
    return False


def validate_artifact(
    settings: Settings,
    path: Path,
    *,
    preset: str,
    duration_ms: int,
    expects_audio: bool,
    runner: ProcessRunner,
) -> PreviewArtifact:
    """Probe, decode every output packet, verify frames, then hash the pinned file."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as file:
            metadata = os.fstat(file.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or not 0 < metadata.st_size <= settings.preview_max_bytes
                or not _faststart(file, metadata.st_size)
            ):
                raise PreviewProcessError("preview_validation_failed")
            file.seek(0)
            input_path = f"/proc/self/fd/{file.fileno()}"
            probe = runner.run(
                [
                    settings.ffprobe_bin,
                    "-v",
                    "error",
                    "-max_alloc",
                    _MAX_ALLOC,
                    *_input_options(settings, "mov"),
                    "-show_entries",
                    "format=format_name,duration,start_time:"
                    "stream=index,codec_type,codec_name,width,height,pix_fmt,avg_frame_rate,"
                    "sample_aspect_ratio,duration,nb_frames,start_time,sample_rate,channels:"
                    "stream_side_data=rotation",
                    "-of",
                    "json",
                    "-i",
                    input_path,
                ],
                pass_fds=(file.fileno(),),
            )
            if probe.returncode:
                raise PreviewProcessError("preview_validation_failed")
            try:
                payload = json.loads(probe.stdout)
            except (ValueError, UnicodeError):
                raise PreviewProcessError("preview_validation_failed") from None
            controlled = validate_metadata(payload, preset, duration_ms, expects_audio)
            decoded = runner.run(
                [
                    *_common(settings),
                    *_input_options(settings, "mov"),
                    "-i",
                    input_path,
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0?",
                    "-threads",
                    str(settings.preview_threads),
                    "-fps_mode",
                    "passthrough",
                    "-f",
                    "null",
                    "-",
                    "-progress",
                    "pipe:1",
                ],
                pass_fds=(file.fileno(),),
            )
            progress = dict(
                line.split("=", 1)
                for line in decoded.stdout.decode("ascii", errors="replace").splitlines()
                if "=" in line
            )
            if (
                decoded.returncode
                or progress.get("progress") != "end"
                or not progress.get("frame", "").strip().isdigit()
                or int(progress["frame"]) != controlled["frame_count"]
                or int(progress["frame"]) < 1
            ):
                raise PreviewProcessError("preview_validation_failed")
            controlled["decoded_frames"] = int(progress["frame"])
            file.seek(0)
            checksum = hashlib.sha256()
            count = 0
            while chunk := file.read(1024 * 1024):
                runner.check()
                checksum.update(chunk)
                count += len(chunk)
            current = os.fstat(file.fileno())
            if count != metadata.st_size or current.st_mtime_ns != metadata.st_mtime_ns:
                raise PreviewProcessError("preview_validation_failed")
            runner.check()
            return PreviewArtifact(path, count, checksum.hexdigest(), controlled)
    except OSError:
        raise PreviewProcessError("preview_storage_unavailable") from None


def render_preview(
    settings: Settings,
    plan: EditSpecV1 | EditSpecV2,
    *,
    source_fd: int,
    source_video_stream_index: int,
    source_has_audio: bool,
    background_fd: int | None,
    preview_start_ms: int,
    duration_ms: int,
    workspace: Path,
    runner: ProcessRunner,
    set_stage: Callable[[str], None],
) -> PreviewArtifact:
    if (
        type(duration_ms) is not int
        or not 1 <= duration_ms <= 8000
        or type(preview_start_ms) is not int
        or preview_start_ms < plan.trim.start_ms
        or preview_start_ms + duration_ms > plan.trim.end_ms
        or type(source_video_stream_index) is not int
        or not 0 <= source_video_stream_index <= 127
        or not workspace.is_absolute()
    ):
        raise PreviewProcessError("preview_invalid_plan")
    audio = plan.audio if isinstance(plan, EditSpecV2) else AudioSpec()
    expects_music = audio.background_audio_id is not None
    if expects_music != (background_fd is not None):
        raise PreviewProcessError("preview_source_unavailable")
    expects_audio = source_has_audio or expects_music
    output = workspace / "preview.part"
    try:
        runner.check()
        text_filters = _text_filters(plan, preview_start_ms, duration_ms, workspace, runner)
        seconds = _seconds(duration_ms)
        argv = [
            *_common(settings),
            "-ss",
            _seconds(preview_start_ms),
            "-noaccurate_seek",
            *_input_options(settings, "mov"),
            "-i",
            f"/proc/self/fd/{source_fd}",
        ]
        descriptors = [source_fd]
        if background_fd is not None:
            if audio.background_loop:
                argv += ["-stream_loop", "-1"]
            argv += [
                "-t",
                _seconds(preview_start_ms - plan.trim.start_ms + duration_ms),
                *_input_options(settings, "mp3"),
                "-i",
                f"/proc/self/fd/{background_fd}",
            ]
            descriptors.append(background_fd)
        video_filters = [
            # Keep demuxer preroll until fps samples the frame covering t=0.
            # Accurate-seek discards that frame at the very end of a source,
            # which otherwise turns a valid final 1 ms window into audio-only.
            # Audio independently discards negative preroll via first_pts=0.
            f"fps=fps={FPS}:start_time=0:round=up:eof_action=pass",
            "trim=start=0",
            f"trim=duration={seconds}",
            "setpts=PTS-STARTPTS",
            framing_filter(plan),
            "format=yuv420p",
            *text_filters,
        ]
        graph = [f"[0:{source_video_stream_index}]{','.join(video_filters)}[video_out]"]
        if expects_audio:
            graph += _audio_graph(
                plan, source_has_audio, expects_music, preview_start_ms, duration_ms
            )
        argv += ["-filter_complex", ";".join(graph), "-map", "[video_out]"]
        if expects_audio:
            argv += [
                "-map",
                "[audio_out]",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-ar",
                "48000",
                "-ac",
                "2",
            ]
        else:
            argv += ["-an"]
        argv += [
            "-sn",
            "-dn",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-metadata:s:v:0",
            "rotate=0",
            "-c:v",
            "libx264",
            "-threads",
            str(settings.preview_threads),
            "-preset",
            "veryfast",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(FPS),
            "-fps_mode",
            "cfr",
            "-t",
            seconds,
            "-movflags",
            "+faststart",
            "-max_muxing_queue_size",
            "256",
            "-f",
            "mp4",
            "-n",
            str(output),
        ]
        set_stage("encoding")
        result = runner.run(argv, pass_fds=tuple(descriptors), cwd=workspace)
        if result.returncode:
            raise PreviewProcessError("preview_encoding_failed")
        set_stage("validating")
        return validate_artifact(
            settings,
            output,
            preset=plan.canvas.preset,
            duration_ms=duration_ms,
            expects_audio=expects_audio,
            runner=runner,
        )
    except FontLayoutError as error:
        raise PreviewProcessError(str(error)) from None
    except OSError:
        raise PreviewProcessError("preview_storage_unavailable") from None
