"""Bounded incremental parsing; real FFmpeg units are checked by runtime QA."""

import pytest

from app.integrations.ffmpeg.progress import MAX_COUNTER, MAX_LINE_BYTES, FFmpegProgress
from app.worker.supervisor import PreviewProcessError


@pytest.mark.parametrize("duration", [0, -1, True, 1.5, "12000", None])
def test_invalid_duration_rejected(duration):
    with pytest.raises(ValueError, match="invalid_progress_duration"):
        FFmpegProgress(duration)


@pytest.mark.parametrize("field", ["out_time_us", "out_time_ms"])
def test_both_timestamp_names_are_microseconds(field):
    updates = []
    parser = FFmpegProgress(12000, updates.append)
    parser.feed(f"{field}=1500123\nprogress=continue\n".encode())
    assert parser.encoded_ms == 1500
    assert updates == [1500]
    assert not parser.ended


def test_modern_timestamp_takes_precedence_over_legacy_alias():
    parser = FFmpegProgress(12000)
    parser.feed(b"out_time_us=2500000\nout_time_ms=9000000\nprogress=continue\n")
    assert parser.encoded_ms == 2500


def test_invalid_modern_timestamp_can_fall_back_to_valid_legacy_alias():
    parser = FFmpegProgress(12000)
    parser.feed(b"out_time_us=N/A\nout_time_ms=2500000\nprogress=continue\n")
    assert parser.encoded_ms == 2500


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 7, 23, 1000])
def test_arbitrary_pipe_fragmentation_and_crlf(chunk_size):
    updates = []
    parser = FFmpegProgress(12000, updates.append)
    payload = (
        b"frame=    30\r\nout_time_us=1000000\r\nprogress=continue\r\n"
        b"frame=90\r\nout_time_us=3000000\r\nprogress=end\r\n"
    )
    for index in range(0, len(payload), chunk_size):
        parser.feed(payload[index : index + chunk_size])
        assert len(parser.buffer) <= MAX_LINE_BYTES
    parser.finish()
    assert updates == [1000, 3000]
    assert parser.frame_count == 90
    assert parser.encoded_ms == 3000
    assert parser.ended
    assert parser.buffer == b""


def test_incomplete_terminal_line_is_consumed_only_at_finish():
    parser = FFmpegProgress(12000)
    parser.feed(b"out_time_us=12000000\nprogress=end")
    assert parser.encoded_ms is None
    assert not parser.ended
    parser.finish()
    assert parser.encoded_ms == 12000
    assert parser.ended
    parser.finish()
    assert parser.ended


def test_progress_emitted_only_on_complete_record_and_values_do_not_regress():
    updates = []
    parser = FFmpegProgress(12000, updates.append)
    parser.feed(b"out_time_us=2000000\n")
    assert updates == []
    parser.feed(b"progress=continue\n")
    parser.feed(b"out_time_us=1000000\nprogress=continue\n")
    parser.feed(b"out_time_us=2000000\nprogress=continue\n")
    parser.feed(b"out_time_us=3500000\nprogress=continue\n")
    assert updates == [2000, 3500]


@pytest.mark.parametrize("value", ["0", "12000000", str(MAX_COUNTER)])
def test_valid_counter_is_bounded_by_requested_duration(value):
    updates = []
    parser = FFmpegProgress(12000, updates.append)
    parser.feed(f"out_time_us={value}\nprogress=continue\n".encode())
    assert parser.encoded_ms == min(12000, int(value) // 1000)
    assert updates == [parser.encoded_ms]
    # A parser reaching the duration does not itself mark a render completed.
    assert not parser.ended


@pytest.mark.parametrize(
    "value",
    ["-1", "+1", "NaN", "Infinity", "1.5", "1e6", "", " 1000", "1000 ", str(MAX_COUNTER + 1)],
)
def test_malformed_timestamps_never_create_progress(value):
    parser = FFmpegProgress(12000)
    parser.feed(f"out_time_us={value}\nprogress=continue\n".encode())
    assert parser.encoded_ms is None


def test_missing_timestamp_does_not_reuse_previous_record():
    updates = []
    parser = FFmpegProgress(12000, updates.append)
    parser.feed(b"out_time_us=1000000\nprogress=continue\nprogress=end\n")
    assert updates == [1000]
    assert parser.ended


def test_unknown_and_private_fields_are_not_retained():
    parser = FFmpegProgress(12000)
    parser.feed(
        b"malformed line\nstream_0_0_q=20.0\nprivate=/private/media.mp4\n"
        b"out_time=00:00:01.000000\nprogress=continue\n"
    )
    assert parser.encoded_ms is None
    assert parser.buffer == b""
    assert "private" not in vars(parser)
    assert "stream_0_0_q" not in vars(parser)


def test_frames_are_monotonic_and_do_not_imply_completion():
    parser = FFmpegProgress(12000)
    parser.feed(b"frame=30\nframe=10\nframe=NaN\nframe=-1\nframe=    90\n")
    assert parser.frame_count == 90
    assert parser.encoded_ms is None
    assert not parser.ended


@pytest.mark.parametrize("value", ["bad", "END", "end /private/path", "", "100%"])
def test_invalid_progress_marker_is_sanitized(value):
    parser = FFmpegProgress(12000)
    with pytest.raises(PreviewProcessError, match="^render_progress_invalid$"):
        parser.feed(f"progress={value}\n".encode())


def test_overlong_line_rejected_with_bounded_retained_tail():
    parser = FFmpegProgress(12000)
    parser.feed(b"x" * MAX_LINE_BYTES)
    with pytest.raises(PreviewProcessError, match="^render_progress_invalid$"):
        parser.feed(b"x")
    assert len(parser.buffer) == MAX_LINE_BYTES


def test_many_short_lines_are_processed_without_accumulating_full_output():
    parser = FFmpegProgress(12000)
    parser.feed(b"speed=1.0x\n" * 10000)
    assert parser.buffer == b""
    assert parser.encoded_ms is None


def test_callback_failure_is_preserved_for_supervisor_cleanup():
    def fail(_encoded_ms):
        raise RuntimeError("progress persistence unavailable")

    parser = FFmpegProgress(12000, fail)
    with pytest.raises(RuntimeError, match="progress persistence unavailable"):
        parser.feed(b"out_time_us=1000000\nprogress=continue\n")
