import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_upload_defaults_are_mib_and_seconds(settings):
    assert settings.upload_max_bytes == 524_288_000
    assert settings.upload_multipart_overhead_bytes == 65_536
    assert settings.media_max_duration_seconds == 600
    assert settings.ffprobe_timeout_seconds == 15
    assert settings.ffprobe_max_output_bytes == 1_048_576
    assert settings.storage_min_free_bytes == 67_108_864


def test_upload_limits_are_environment_configurable(monkeypatch):
    monkeypatch.setenv("EDIVO_UPLOAD_MAX_BYTES", "1048576")
    monkeypatch.setenv("EDIVO_MEDIA_MAX_DURATION_SECONDS", "30")
    monkeypatch.setenv("EDIVO_FFPROBE_TIMEOUT_SECONDS", "3")
    settings = Settings(_env_file=None)
    assert settings.upload_max_bytes == 1_048_576
    assert settings.media_max_duration_seconds == 30
    assert settings.ffprobe_timeout_seconds == 3


@pytest.mark.parametrize(
    "field,value",
    [
        ("upload_max_bytes", 0),
        ("upload_max_bytes", 2 * 1024**3 + 1),
        ("upload_multipart_overhead_bytes", 0),
        ("upload_multipart_overhead_bytes", 1024 * 1024 + 1),
        ("media_max_duration_seconds", 0),
        ("media_max_duration_seconds", 86401),
        ("ffprobe_timeout_seconds", 0),
        ("ffprobe_timeout_seconds", 121),
        ("ffprobe_max_output_bytes", 0),
        ("ffprobe_max_output_bytes", 8 * 1024 * 1024 + 1),
        ("storage_min_free_bytes", -1),
    ],
)
def test_upload_limits_reject_invalid_configuration(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})
