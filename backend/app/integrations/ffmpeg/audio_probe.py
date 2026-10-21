"""Restricted MP3 inspection; originals are read without modifying their bytes."""

import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.core.errors import AppError
from app.integrations.ffmpeg.probe import run_bounded

_MPEG1_BITRATES = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
_MPEG2_BITRATES = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
_SAMPLE_RATES = {8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000}


def invalid_audio() -> AppError:
    return AppError(
        422, "invalid_audio", "O MP3 está inválido, corrompido ou sem áudio utilizável."
    )


def _unsupported_audio() -> AppError:
    return AppError(
        415, "unsupported_audio", "Envie um MP3 com apenas uma faixa de áudio e sem vídeo."
    )


@dataclass(frozen=True)
class MP3Structure:
    duration: Fraction
    max_frame_duration: Fraction
    sample_rates: frozenset[int]
    channels: frozenset[int]


def _frame_info(header: bytes) -> tuple[int, int, int, int]:
    if len(header) != 4:
        raise invalid_audio()
    bits = int.from_bytes(header)
    version, layer = (bits >> 19) & 3, (bits >> 17) & 3
    bitrate_index, rate_index = (bits >> 12) & 15, (bits >> 10) & 3
    if (
        bits >> 21 != 0x7FF
        or version == 1
        or layer != 1
        or bitrate_index in {0, 15}
        or rate_index == 3
        or bits & 3 == 2
    ):
        raise invalid_audio()
    sample_rate = (44100, 48000, 32000)[rate_index]
    if version != 3:
        sample_rate //= 2 if version == 2 else 4
    bitrate = (_MPEG1_BITRATES if version == 3 else _MPEG2_BITRATES)[bitrate_index]
    length = (144000 if version == 3 else 72000) * bitrate // sample_rate + ((bits >> 9) & 1)
    samples = 1152 if version == 3 else 576
    channels = 1 if (bits >> 6) & 3 == 3 else 2
    return length, samples, sample_rate, channels


def validate_mp3_structure(path: Path, max_duration_seconds: int | None = None) -> MP3Structure:
    """Walk complete Layer III frame boundaries, allowing ID3v2 and trailing ID3v1.

    This checks framing throughout the file, not the decoding of every payload.
    Free-format streams and additional trailing tag formats are outside this importer.
    """
    try:
        size = path.stat().st_size
        if size < 4:
            raise invalid_audio()
        with path.open("rb") as source:
            prefix = source.read(10)
            offset = 0
            if prefix[:3] == b"ID3":
                if len(prefix) != 10 or prefix[3] not in {2, 3, 4} or prefix[4] == 255:
                    raise invalid_audio()
                allowed_flags = {2: 0xC0, 3: 0xE0, 4: 0xF0}[prefix[3]]
                if prefix[5] & ~allowed_flags or any(byte & 0x80 for byte in prefix[6:10]):
                    raise invalid_audio()
                tag_size = 0
                for byte in prefix[6:10]:
                    tag_size = (tag_size << 7) | byte
                offset = 10 + tag_size
                if prefix[3] == 4 and prefix[5] & 0x10:
                    source.seek(offset)
                    footer = source.read(10)
                    if footer != b"3DI" + prefix[3:]:
                        raise invalid_audio()
                    offset += 10
                if offset >= size:
                    raise invalid_audio()
            elif prefix[0] != 0xFF or prefix[1] & 0xE0 != 0xE0:
                raise _unsupported_audio()

            frames = 0
            duration = Fraction(0)
            max_frame_duration = Fraction(0)
            sample_rates: set[int] = set()
            channel_counts: set[int] = set()
            while offset < size:
                source.seek(offset)
                header = source.read(4)
                if size - offset == 128 and header[:3] == b"TAG":
                    offset = size
                    break
                frames += 1
                if frames > 100_000:
                    raise invalid_audio()
                length, samples, sample_rate, channels = _frame_info(header)
                if offset + length > size:
                    raise invalid_audio()
                frame_duration = Fraction(samples, sample_rate)
                duration += frame_duration
                max_frame_duration = max(max_frame_duration, frame_duration)
                sample_rates.add(sample_rate)
                channel_counts.add(channels)
                # A Xing/Info frame and encoder padding can add at most two frames
                # to the playable duration. Declared tag durations are untrusted.
                if (
                    max_duration_seconds is not None
                    and duration > max_duration_seconds + 2 * max_frame_duration
                ):
                    raise AppError(
                        422,
                        "audio_duration_exceeded",
                        "A duração do áudio excede o limite permitido.",
                    )
                offset += length
            if not frames:
                raise invalid_audio()
            return MP3Structure(
                duration, max_frame_duration, frozenset(sample_rates), frozenset(channel_counts)
            )
    except OSError:
        raise invalid_audio() from None


@dataclass(frozen=True)
class AudioMetadata:
    duration_ms: int
    codec: str
    sample_rate: int
    channels: int


def _integer(value: Any, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise invalid_audio()
    try:
        number = int(value)
        if str(number) != str(value) or not minimum <= number <= maximum:
            raise ValueError
        return number
    except (TypeError, ValueError, OverflowError):
        raise invalid_audio() from None


def normalize_audio_metadata(payload: Any, max_duration_seconds: int) -> AudioMetadata:
    """Keep only the four public, validated MP3 metadata fields."""
    try:
        if not isinstance(payload, dict):
            raise invalid_audio()
        streams, format_info = payload.get("streams"), payload.get("format")
        if not isinstance(streams, list) or not isinstance(format_info, dict):
            raise invalid_audio()
        if format_info.get("format_name") != "mp3":
            raise _unsupported_audio()
        if not streams or any(not isinstance(stream, dict) for stream in streams):
            raise invalid_audio()
        # No stream selection: even an attached cover picture makes this unsupported.
        if len(streams) != 1 or streams[0].get("codec_type") != "audio":
            raise _unsupported_audio()
        audio = streams[0]
        if audio.get("codec_name") != "mp3":
            raise _unsupported_audio()
        _integer(audio.get("index"), 0, 127)
        sample_rate = _integer(audio.get("sample_rate"), 1, 48000)
        if sample_rate not in _SAMPLE_RATES:
            raise invalid_audio()
        channels = _integer(audio.get("channels"), 1, 2)
        durations = []
        for value in (format_info.get("duration"), audio.get("duration")):
            if value not in (None, "N/A"):
                duration = Decimal(str(value))
                if not duration.is_finite() or duration <= 0:
                    raise invalid_audio()
                durations.append(duration)
        if not durations:
            raise invalid_audio()
        duration = max(durations)
        if duration > max_duration_seconds:
            raise AppError(
                422, "audio_duration_exceeded", "A duração do áudio excede o limite permitido."
            )
        duration_ms = int((duration * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if duration_ms <= 0:
            raise invalid_audio()
        return AudioMetadata(duration_ms, "mp3", sample_rate, channels)
    except AppError:
        raise
    except (ValueError, TypeError, InvalidOperation, OverflowError):
        raise invalid_audio() from None


def _validate_decoded_prefix(payload: dict[str, Any], metadata: AudioMetadata) -> None:
    frames = payload.get("frames")
    if not isinstance(frames, list) or not frames or len(frames) > 512:
        raise invalid_audio()
    stream_index = _integer(payload["streams"][0]["index"], 0, 127)
    decoded_samples = 0
    for frame in frames:
        if (
            not isinstance(frame, dict)
            or frame.get("media_type") != "audio"
            or _integer(frame.get("stream_index"), 0, 127) != stream_index
        ):
            raise invalid_audio()
        decoded_samples += _integer(frame.get("nb_samples"), 1, 1152)
    expected_samples = min(metadata.duration_ms, 2000) * metadata.sample_rate // 1000
    # Container duration can include MP3 encoder delay/padding. Permit at most two
    # MPEG-1 frames or 10% of the sampled interval, while requiring actual samples.
    allowance = max(2304, expected_samples // 10)
    if decoded_samples < max(1, expected_samples - allowance):
        raise invalid_audio()


class FFprobeAudioInspector:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def inspect(self, path: Path) -> AudioMetadata:
        structure = validate_mp3_structure(path, self.settings.audio_max_duration_seconds)
        entries = (
            "format=format_name,duration:"
            "stream=index,codec_type,codec_name,sample_rate,channels,duration:"
            "frame=media_type,stream_index,nb_samples"
        )
        try:
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
                    "mp3",
                    "-probesize",
                    "5242880",
                    "-analyzeduration",
                    "5000000",
                    "-err_detect",
                    "explode",
                    "-read_intervals",
                    "%+2",
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
        except AppError as error:
            if error.status_code == 503:
                raise AppError(
                    503, error.code, "A análise de áudio está temporariamente indisponível."
                ) from None
            raise invalid_audio() from None
        if code != 0:
            raise invalid_audio()
        try:
            payload = json.loads(output)
        except (ValueError, UnicodeError):
            raise invalid_audio() from None
        metadata = normalize_audio_metadata(payload, self.settings.audio_max_duration_seconds)
        # FFprobe can trust a forged Xing/Info frame count even when all physical
        # frames are present. Compare its exact duration against the independent
        # framing clock, including short inputs below the configured upper limit.
        declared_duration = max(
            Fraction(Decimal(str(value)))
            for value in (payload["format"].get("duration"), payload["streams"][0].get("duration"))
            if value not in (None, "N/A")
        )
        if (
            abs(declared_duration - structure.duration) > 2 * structure.max_frame_duration
            or metadata.sample_rate not in structure.sample_rates
            or metadata.channels not in structure.channels
        ):
            raise invalid_audio()
        _validate_decoded_prefix(payload, metadata)
        return metadata
