"""Bounded, cached runtime inventory; never executes user media or shell commands."""

import re
import subprocess
from threading import Lock
from time import monotonic
from typing import Literal

from pydantic import BaseModel

from app.core.config import Settings

Reason = Literal["not_found", "timeout", "command_failed", "invalid_output", "not_detected"]


class Capability(BaseModel):
    available: bool
    reason: Reason | None = None


class BinaryCapability(Capability):
    version: str | None = None


class EncoderCapabilities(BaseModel):
    h264: Capability
    aac: Capability
    h264_encoder: Literal["libx264"] = "libx264"


class FilterCapabilities(BaseModel):
    scale: Capability
    crop: Capability
    pad: Capability
    drawtext: Capability


class CapabilitiesReport(BaseModel):
    status: Literal["available", "unavailable"]
    runtime: Literal["api", "worker"]
    ffmpeg: BinaryCapability
    ffprobe: BinaryCapability
    encoders: EncoderCapabilities
    filters: FilterCapabilities


class CapabilityDetector:
    def __init__(self, settings: Settings, runtime: Literal["api", "worker"] = "api") -> None:
        self.settings = settings
        self.runtime = runtime
        self._lock = Lock()
        self._cached: CapabilitiesReport | None = None
        self._cached_at = 0.0

    def _run(self, binary: str, *args: str) -> tuple[str, Reason | None]:
        try:
            result = subprocess.run(
                [binary, "-hide_banner", *args],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=self.settings.ffmpeg_timeout_seconds,
                check=False,
            )
        except FileNotFoundError:
            return "", "not_found"
        except subprocess.TimeoutExpired:
            return "", "timeout"
        except OSError:
            return "", "command_failed"
        return (result.stdout, None) if result.returncode == 0 else ("", "command_failed")

    def _binary(self, binary: str, name: str) -> BinaryCapability:
        output, reason = self._run(binary, "-version")
        if reason:
            return BinaryCapability(available=False, reason=reason)
        # Include only the version token, never raw build flags, paths or stderr.
        match = re.match(rf"{name} version ([\w.+~-]+)(?:\s|$)", output)
        if not match:
            return BinaryCapability(available=False, reason="invalid_output")
        return BinaryCapability(available=True, version=match.group(1))

    @staticmethod
    def _item(output: str, name: str, reason: Reason | None) -> Capability:
        available = reason is None and any(
            len(tokens := line.split()) >= 2 and tokens[1] == name for line in output.splitlines()
        )
        return Capability(
            available=available, reason=None if available else reason or "not_detected"
        )

    def get(self) -> CapabilitiesReport:
        with self._lock:
            if (
                self._cached is not None
                and monotonic() - self._cached_at < self.settings.capabilities_cache_seconds
            ):
                return self._cached.model_copy(deep=True)
            ffmpeg = self._binary(self.settings.ffmpeg_bin, "ffmpeg")
            ffprobe = self._binary(self.settings.ffprobe_bin, "ffprobe")
            encoder_output, encoder_reason = (
                self._run(self.settings.ffmpeg_bin, "-encoders")
                if ffmpeg.available
                else ("", ffmpeg.reason)
            )
            filter_output, filter_reason = (
                self._run(self.settings.ffmpeg_bin, "-filters")
                if ffmpeg.available
                else ("", ffmpeg.reason)
            )
            encoders = EncoderCapabilities(
                h264=self._item(encoder_output, "libx264", encoder_reason),
                aac=self._item(encoder_output, "aac", encoder_reason),
            )
            filters = FilterCapabilities(
                **{
                    name: self._item(filter_output, name, filter_reason)
                    for name in ("scale", "crop", "pad", "drawtext")
                }
            )
            all_available = all(
                [
                    ffmpeg.available,
                    ffprobe.available,
                    encoders.h264.available,
                    encoders.aac.available,
                    *(getattr(filters, name).available for name in type(filters).model_fields),
                ]
            )
            self._cached = CapabilitiesReport(
                status="available" if all_available else "unavailable",
                runtime=self.runtime,
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
                encoders=encoders,
                filters=filters,
            )
            self._cached_at = monotonic()
            return self._cached.model_copy(deep=True)
