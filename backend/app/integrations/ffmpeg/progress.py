"""Incremental, bounded FFmpeg -progress parser (timestamps are microseconds).

The installed FFmpeg 5.1 emits both out_time_us and its historical alias
out_time_ms in MICROSECONDS. Never treat the latter as milliseconds. No raw
output, command, media tag or pathname is retained or logged here.
"""

from collections.abc import Callable

from app.worker.supervisor import PreviewProcessError

MAX_LINE_BYTES = 4096
MAX_COUNTER = 2**63 - 1


class FFmpegProgress:
    def __init__(self, duration_ms: int, on_progress: Callable[[int], None] | None = None) -> None:
        if type(duration_ms) is not int or duration_ms <= 0:
            raise ValueError("invalid_progress_duration")
        self.duration_ms = duration_ms
        self.on_progress = on_progress
        self.buffer = bytearray()
        self.encoded_ms: int | None = None
        self.frame_count = 0
        self.ended = False
        self._time_us: int | None = None
        self._legacy_us: int | None = None

    def feed(self, data: bytes) -> None:
        # Consume lines before accepting more data; the pending tail is bounded
        # even if the process writes one huge line over many pipe reads.
        for fragment in data.splitlines(keepends=True):
            if len(self.buffer) + len(fragment) > MAX_LINE_BYTES:
                raise PreviewProcessError("render_progress_invalid")
            self.buffer.extend(fragment)
            if self.buffer.endswith(b"\n"):
                self._line(bytes(self.buffer).rstrip(b"\r\n"))
                self.buffer.clear()

    @staticmethod
    def _counter(value: bytes) -> int | None:
        if not value or len(value) > 19 or not value.isdigit():
            return None
        number = int(value)
        return number if number <= MAX_COUNTER else None

    def _line(self, line: bytes) -> None:
        key, separator, value = line.partition(b"=")
        if not separator:
            return
        if key == b"frame":
            number = self._counter(value.strip())
            if number is not None:
                self.frame_count = max(self.frame_count, number)
        elif key == b"out_time_us":
            self._time_us = self._counter(value)
        elif key == b"out_time_ms":
            self._legacy_us = self._counter(value)
        elif key == b"progress":
            if value not in (b"continue", b"end"):
                raise PreviewProcessError("render_progress_invalid")
            timestamp = self._time_us if self._time_us is not None else self._legacy_us
            if timestamp is not None:
                measured_ms = min(self.duration_ms, timestamp // 1000)
                if self.encoded_ms is None or measured_ms > self.encoded_ms:
                    self.encoded_ms = measured_ms
                    if self.on_progress is not None:
                        self.on_progress(measured_ms)
            self.ended |= value == b"end"
            self._time_us = self._legacy_us = None

    def finish(self) -> None:
        if self.buffer:
            self._line(bytes(self.buffer).rstrip(b"\r"))
            self.buffer.clear()
