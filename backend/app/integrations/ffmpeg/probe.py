"""Restricted MP4 inspection with bounded subprocess output, time and metadata."""

import json
import os
import re
import selectors
import signal
import struct
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from time import monotonic
from typing import Any

from app.core.config import Settings
from app.core.errors import AppError

MP4_BRANDS = {b"isom", b"mp41", b"mp42", b"avc1", b"M4V ", b"MSNV", b"dash"} | {
    f"iso{number}".encode() for number in range(2, 10)
}


def invalid_media() -> AppError:
    return AppError(
        422, "invalid_media", "O MP4 está inválido, corrompido ou sem vídeo utilizável."
    )


def validate_mp4_structure(path: Path) -> None:
    """Walk top-level ISO BMFF boxes without reading media payloads into memory."""
    size = path.stat().st_size
    offset = 0
    boxes = 0
    found: set[bytes] = set()
    with path.open("rb") as source:
        while offset < size:
            boxes += 1
            if boxes > 100_000 or size - offset < 8:
                raise invalid_media()
            source.seek(offset)
            box_size, kind = struct.unpack(">I4s", source.read(8))
            header_size = 8
            if box_size == 1:
                if size - offset < 16:
                    raise invalid_media()
                box_size = struct.unpack(">Q", source.read(8))[0]
                header_size = 16
            elif box_size == 0:
                # Only a final media-data box may extend to end-of-file.
                if kind != b"mdat":
                    raise invalid_media()
                box_size = size - offset
            if box_size < header_size or offset + box_size > size:
                raise invalid_media()
            if kind == b"ftyp":
                if kind in found or offset > 65536:
                    raise invalid_media()
                payload_size = box_size - header_size
                if payload_size < 8 or payload_size > 1024 or payload_size % 4:
                    raise invalid_media()
                payload = source.read(payload_size)
                if payload[:4] not in MP4_BRANDS:
                    raise AppError(
                        415, "unsupported_media", "O container informado não é MP4 suportado."
                    )
            found.add(kind)
            offset += box_size
    if b"ftyp" not in found:
        raise AppError(415, "unsupported_media", "O conteúdo do arquivo não é um container MP4.")
    if not {b"moov", b"mdat"}.issubset(found):
        raise invalid_media()


def run_bounded(argv: list[str], timeout: float, output_limit: int) -> tuple[int, bytes]:
    """Drain both pipes under one wall-clock deadline; never buffer unbounded stderr."""
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError:
        raise AppError(
            503, "probe_unavailable", "A análise de vídeo está temporariamente indisponível."
        ) from None

    stdout = bytearray()
    output_size = 0
    deadline = monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            assert process.stdout is not None and process.stderr is not None
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise AppError(
                        503, "probe_timeout", "A análise de vídeo excedeu o tempo permitido."
                    )
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fileobj.fileno(), min(65536, output_limit + 1))
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    output_size += len(chunk)
                    if output_size > output_limit:
                        raise AppError(
                            422, "invalid_media", "A mídia excedeu os limites de análise."
                        )
                    if key.data == "stdout":
                        stdout.extend(chunk)
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise AppError(
                    503, "probe_timeout", "A análise de vídeo excedeu o tempo permitido."
                )
            try:
                return process.wait(timeout=remaining), bytes(stdout)
            except subprocess.TimeoutExpired:
                raise AppError(
                    503, "probe_timeout", "A análise de vídeo excedeu o tempo permitido."
                ) from None
    finally:
        if process.poll() is None:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()


@dataclass(frozen=True)
class MediaMetadata:
    duration_ms: int
    width: int
    height: int
    fps_num: int
    fps_den: int
    video_codec: str
    has_audio: bool
    audio_codec: str | None
    probe_metadata: dict[str, Any]


def _positive_integer(value: Any) -> int:
    if isinstance(value, bool):
        raise invalid_media()
    try:
        number = int(value)
        if str(number) != str(value) or not 0 < number <= 2_147_483_647:
            raise ValueError
        return number
    except (TypeError, ValueError, OverflowError):
        raise invalid_media() from None


def _codec(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_]{1,64}", value):
        raise invalid_media()
    return value


def normalize_metadata(payload: Any, max_duration_seconds: int) -> MediaMetadata:
    try:
        if not isinstance(payload, dict):
            raise invalid_media()
        streams = payload.get("streams")
        format_info = payload.get("format")
        if not isinstance(streams, list) or len(streams) > 128 or not isinstance(format_info, dict):
            raise invalid_media()
        if "mp4" not in str(format_info.get("format_name", "")).split(","):
            raise AppError(415, "unsupported_media", "O conteúdo detectado não é MP4.")
        if any(not isinstance(stream, dict) for stream in streams):
            raise invalid_media()
        videos = [
            stream
            for stream in streams
            if stream.get("codec_type") == "video"
            and not stream.get("disposition", {}).get("attached_pic", 0)
        ]
        if not videos:
            raise invalid_media()
        # Choose default stream first, then lowest stream index. Cover art is never a source.
        videos.sort(
            key=lambda stream: (
                not bool(stream.get("disposition", {}).get("default", 0)),
                int(stream["index"]),
            )
        )
        video = videos[0]
        width, height = (
            _positive_integer(video.get("width")),
            _positive_integer(video.get("height")),
        )
        codec = _codec(video.get("codec_name"))
        fps = None
        for field in ("avg_frame_rate", "r_frame_rate"):
            try:
                candidate = Fraction(str(video.get(field)))
            except (ValueError, ZeroDivisionError):
                continue
            if candidate > 0:
                fps = candidate
                break
        if fps is None or max(fps.numerator, fps.denominator) > 2_147_483_647:
            raise invalid_media()
        durations = []
        for value in (format_info.get("duration"), video.get("duration")):
            if value not in (None, "N/A"):
                duration = Decimal(str(value))
                if not duration.is_finite() or duration <= 0:
                    raise invalid_media()
                durations.append(duration)
        if not durations:
            raise invalid_media()
        duration = max(durations)
        if duration > max_duration_seconds:
            raise AppError(
                422, "media_duration_exceeded", "A duração do vídeo excede o limite permitido."
            )
        duration_ms = int((duration * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if duration_ms <= 0:
            raise invalid_media()
        audio = sorted(
            [stream for stream in streams if stream.get("codec_type") == "audio"],
            key=lambda stream: int(stream["index"]),
        )
        audio_codec = _codec(audio[0].get("codec_name")) if audio else None
        # All keys and values are controlled here: no tags, URLs, filename or side data.
        safe_metadata = {
            "format": "mp4",
            "video_stream_index": int(video["index"]),
            "stream_count": len(streams),
            "duration_ms": duration_ms,
            "width": width,
            "height": height,
            "fps_num": fps.numerator,
            "fps_den": fps.denominator,
            "video_codec": codec,
            "has_audio": bool(audio),
            "audio_codec": audio_codec,
        }
        return MediaMetadata(
            duration_ms,
            width,
            height,
            fps.numerator,
            fps.denominator,
            codec,
            bool(audio),
            audio_codec,
            safe_metadata,
        )
    except AppError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, InvalidOperation, OverflowError):
        raise invalid_media() from None


class FFprobeInspector:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def inspect(self, path: Path) -> MediaMetadata:
        validate_mp4_structure(path)
        entries = (
            "format=format_name,duration:"
            "stream=index,codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate,duration:"
            "stream_disposition=default,attached_pic:frame=media_type,stream_index"
        )
        code, output = run_bounded(
            [
                self.settings.ffprobe_bin,
                "-v",
                "error",
                "-max_alloc",
                "67108864",
                "-threads",
                "1",
                "-protocol_whitelist",
                "file",
                "-format_whitelist",
                "mov",
                "-probesize",
                "5242880",
                "-analyzeduration",
                "5000000",
                "-enable_drefs",
                "0",
                "-use_absolute_path",
                "0",
                "-read_intervals",
                "%+0.1",
                "-show_frames",
                "-show_entries",
                entries,
                "-of",
                "json",
                "-i",
                str(path.absolute()),
            ],
            self.settings.ffprobe_timeout_seconds,
            self.settings.ffprobe_max_output_bytes,
        )
        if code != 0:
            raise invalid_media()
        try:
            payload = json.loads(output)
        except (ValueError, UnicodeError):
            raise invalid_media() from None
        metadata = normalize_metadata(payload, self.settings.media_max_duration_seconds)
        # Decoding a short bounded prefix rejects streams whose headers claim video but
        # whose data yields no video frame. Full-file decoding belongs to later QA.
        frames = payload.get("frames", [])
        if not isinstance(frames, list) or not any(
            isinstance(frame, dict)
            and frame.get("media_type") == "video"
            and frame.get("stream_index") == metadata.probe_metadata["video_stream_index"]
            for frame in frames
        ):
            raise invalid_media()
        return metadata
