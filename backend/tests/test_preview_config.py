"""Preview resource/lease bounds reject configurations that cannot fence safely."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings


@pytest.mark.parametrize(
    "patch",
    [
        {"preview_heartbeat_seconds": 15},
        {"preview_lease_seconds": 10, "preview_heartbeat_seconds": 5},
        {"preview_lease_seconds": 1},
        {"preview_heartbeat_seconds": 0},
        {"preview_worker_poll_seconds": 0},
        {"preview_ffmpeg_timeout_seconds": 121},
        {"preview_ffmpeg_timeout_seconds": float("nan")},
        {"preview_lease_seconds": float("inf")},
        {"preview_heartbeat_seconds": float("nan")},
        {"preview_max_attempts": 3},
        {"preview_max_active_per_project": 3},
        {"preview_threads": 0},
        {"preview_memory_bytes": 1024},
        {"preview_cpu_seconds": 0},
        {"preview_max_bytes": 0},
        {"preview_process_output_bytes": 0},
    ],
)
def test_unsafe_preview_config_is_rejected(settings, patch):
    with pytest.raises(ValidationError):
        Settings.model_validate({**settings.model_dump(), **patch})


def test_bounded_preview_defaults_and_short_test_lease(settings):
    assert settings.preview_max_bytes == 64 * 1024 * 1024
    assert settings.preview_ffmpeg_timeout_seconds == 120
    assert settings.preview_threads == 2
    assert settings.preview_max_attempts == settings.preview_max_active_per_project == 2
    assert settings.preview_heartbeat_seconds < settings.preview_lease_seconds / 2
    short = Settings.model_validate(
        {**settings.model_dump(), "preview_lease_seconds": 2, "preview_heartbeat_seconds": 0.25}
    )
    assert short.preview_heartbeat_seconds == 0.25
