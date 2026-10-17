import hashlib
import io
from uuid import uuid4

import pytest

from app.core.errors import AppError
from app.storage.local import LocalStorage, sanitize_filename


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("../../private/video.mp4", "video.mp4"),
        (r"C:\private\video.MP4", "video.MP4"),
        ("hello\x00\r\n.mp4", "hello.mp4"),
        ("a<b>.mp4", "a_b_.mp4"),
        ("../..", "video.mp4"),
    ],
)
def test_sanitized_display_name(filename, expected):
    assert sanitize_filename(filename) == expected
    assert len(sanitize_filename("x" * 500 + ".mp4")) == 160


def test_storage_hash_publication_and_no_overwrite(settings):
    storage = LocalStorage(settings)
    data = b"synthetic-bytes" * 1000
    staged = storage.stage(io.BytesIO(data))
    assert staged.byte_size == len(data)
    assert staged.checksum_sha256 == hashlib.sha256(data).hexdigest()
    project_id, asset_id = uuid4(), uuid4()
    key = storage.promote(staged, project_id, asset_id)
    target = settings.storage_root / key
    assert target.read_bytes() == data
    assert target.stat().st_mode & 0o777 == 0o400
    assert not staged.path.exists()
    replacement = storage.stage(io.BytesIO(b"new-data"))
    with pytest.raises(AppError):
        storage.promote(replacement, project_id, asset_id)
    assert target.read_bytes() == data
    storage.discard_staging(replacement.path)


def test_storage_rejects_symlinked_originals(settings, tmp_path):
    storage = LocalStorage(settings)
    staged = storage.stage(io.BytesIO(b"content"))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (settings.storage_root / "originals").symlink_to(elsewhere)
    with pytest.raises(AppError) as error:
        storage.promote(staged, uuid4(), uuid4())
    assert error.value.status_code == 503
    assert list(elsewhere.iterdir()) == []
    storage.discard_staging(staged.path)


@pytest.mark.parametrize("payload,limit,status", [(b"", 100, 422), (b"x" * 11, 10, 413)])
def test_staging_failure_cleans_partial(settings, payload, limit, status):
    settings.upload_max_bytes = limit
    storage = LocalStorage(settings)
    with pytest.raises(AppError) as error:
        storage.stage(io.BytesIO(payload))
    assert error.value.status_code == status
    assert list((settings.storage_root / "temp").iterdir()) == []


def test_storage_reserve_and_missing_root(settings, monkeypatch):
    monkeypatch.setattr(
        "app.storage.local.shutil.disk_usage", lambda _: type("Usage", (), {"free": 0})()
    )
    with pytest.raises(AppError) as error:
        LocalStorage(settings).prepare_temp()
    assert error.value.status_code == 503
    settings.storage_root /= "absent-root"
    with pytest.raises(AppError):
        LocalStorage(settings).prepare_temp()
    assert not settings.storage_root.exists()
