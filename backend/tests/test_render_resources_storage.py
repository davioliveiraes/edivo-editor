"""Render resource, private storage and attachment contracts using synthetic bytes.

These units exercise filesystem/ASGI boundaries, not FFmpeg or MP4 playback.
Database authorization and actual media composition have separate test gates.
"""

import asyncio
import hashlib
import io
import os
import stat
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.media_stream import PrivateMediaResponse
from app.core.config import Settings
from app.core.errors import AppError
from app.storage.local import LocalStorage, StagedFile

ARTIFACT = bytes(range(256)) * 701
FILENAME = "edivo-r8-9x16-standard.mp4"


@pytest.mark.parametrize("method", ["promote_render", "promote_preview"])
def test_publication_rejects_fifo_staging_without_blocking(settings, tmp_path, monkeypatch, method):
    settings.storage_min_free_bytes = 0
    storage = LocalStorage(settings)
    fifo = tmp_path / "unexpected-fifo"
    os.mkfifo(fifo, mode=0o600)
    original_open = os.open

    def guarded_open(path, flags, *args, **kwargs):
        if path == fifo:
            # Fail promptly even on a regression; never hang the test process.
            assert flags & os.O_NONBLOCK
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", guarded_open)
    with pytest.raises(AppError) as error:
        getattr(storage, method)(StagedFile(fifo, 1, "0" * 64), uuid4(), uuid4())
    assert error.value.status_code == 503
    assert error.value.code == "storage_unavailable"
    assert stat.S_ISFIFO(fifo.stat().st_mode)


def test_render_budgets_are_independent_from_preserved_preview_defaults(settings):
    assert settings.render_timeout_seconds == settings.render_cpu_seconds == 3600
    assert settings.render_max_output_bytes == 2 * 1024**3
    assert settings.render_worker_concurrency == settings.render_max_active_per_project == 1
    assert settings.render_max_active_total == 20
    assert settings.render_poll_seconds == 2
    assert settings.render_max_attempts == 2
    assert settings.render_lease_seconds == 30
    assert settings.render_heartbeat_seconds == 5
    assert settings.render_cancel_poll_seconds == 0.5
    assert settings.render_terminate_grace_seconds == 2
    assert settings.render_memory_bytes == 1024**3
    assert settings.render_threads == 2
    assert settings.render_process_output_bytes == 256 * 1024
    assert settings.render_progress_output_bytes == 16 * 1024 * 1024
    assert settings.preview_ffmpeg_timeout_seconds == settings.preview_cpu_seconds == 120
    assert settings.preview_max_bytes == 64 * 1024 * 1024
    assert settings.preview_max_active_per_project == settings.preview_max_attempts == 2
    assert settings.preview_worker_poll_seconds == 1.5
    assert settings.preview_lease_seconds == 30
    assert settings.preview_heartbeat_seconds == 5
    reduced = Settings.model_validate(
        {
            **settings.model_dump(),
            "render_timeout_seconds": 60,
            "render_cpu_seconds": 60,
            "render_max_output_bytes": 1024 * 1024,
            "render_max_attempts": 1,
            "render_lease_seconds": 2,
            "render_heartbeat_seconds": 0.25,
            "render_cancel_poll_seconds": 0.25,
        }
    )
    assert reduced.render_max_output_bytes == 1024 * 1024
    assert reduced.preview_max_bytes == settings.preview_max_bytes
    assert reduced.preview_ffmpeg_timeout_seconds == 120


@pytest.mark.parametrize(
    "patch",
    [
        {"render_timeout_seconds": 0},
        {"render_timeout_seconds": 3601},
        {"render_max_output_bytes": 1023},
        {"render_max_output_bytes": 2 * 1024**3 + 1},
        {"render_max_active_per_project": 0},
        {"render_max_active_per_project": 2},
        {"render_max_active_total": 0},
        {"render_max_active_total": 1001},
        {"render_worker_concurrency": 0},
        {"render_worker_concurrency": 2},
        {"render_poll_seconds": 0},
        {"render_poll_seconds": 31},
        {"render_max_attempts": 0},
        {"render_max_attempts": 3},
        {"render_lease_seconds": 1},
        {"render_lease_seconds": 301},
        {"render_heartbeat_seconds": 0},
        {"render_heartbeat_seconds": 15},
        {"render_lease_seconds": 10, "render_heartbeat_seconds": 5},
        {
            "render_lease_seconds": 2,
            "render_heartbeat_seconds": 0.25,
            "render_cancel_poll_seconds": 1,
        },
        {"render_cancel_poll_seconds": 0},
        {"render_cancel_poll_seconds": 2.1},
        {"render_terminate_grace_seconds": 0},
        {"render_terminate_grace_seconds": 5.1},
        {"render_threads": 0},
        {"render_threads": 5},
        {"render_memory_bytes": 1024},
        {"render_memory_bytes": 4 * 1024**3 + 1},
        {"render_cpu_seconds": 0},
        {"render_cpu_seconds": 3601},
        {"render_process_output_bytes": 0},
        {"render_process_output_bytes": 8 * 1024 * 1024 + 1},
        {"render_progress_output_bytes": 0},
        {"render_progress_output_bytes": 32 * 1024 * 1024 + 1},
    ],
)
def test_unsafe_render_resource_and_lease_configuration_is_rejected(settings, patch):
    with pytest.raises(ValidationError):
        Settings.model_validate({**settings.model_dump(), **patch})


@pytest.mark.parametrize(
    "field",
    [
        "render_timeout_seconds",
        "render_poll_seconds",
        "render_lease_seconds",
        "render_heartbeat_seconds",
        "render_cancel_poll_seconds",
        "render_terminate_grace_seconds",
    ],
)
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_render_times_are_rejected(settings, field, value):
    with pytest.raises(ValidationError):
        Settings.model_validate({**settings.model_dump(), field: value})


@pytest.fixture
def render_storage(settings):
    # Test artifacts use bytes, not the 2 GiB production output budget.
    settings.storage_min_free_bytes = 0
    storage = LocalStorage(settings)
    staged = storage.stage(io.BytesIO(ARTIFACT))
    project_id, job_id = uuid4(), uuid4()
    checksum = hashlib.sha256(ARTIFACT).hexdigest()
    key = storage.promote_render(staged, project_id, job_id)
    return storage, project_id, job_id, key, checksum


def assert_unavailable(error):
    assert error.value.status_code == 503
    assert error.value.code == "storage_unavailable"
    assert "/" not in error.value.message
    assert "Traceback" not in error.value.message


def test_render_publication_is_exclusive_private_and_preserves_source_namespaces(settings):
    settings.storage_min_free_bytes = 0
    storage = LocalStorage(settings)
    project_id = uuid4()
    originals = []
    for publish, content in (
        (storage.promote, b"synthetic-mp4-original"),
        (storage.promote_audio, b"synthetic-mp3-original"),
        (storage.promote_preview, b"synthetic-r04-preview"),
    ):
        staged = storage.stage(io.BytesIO(content))
        key = publish(staged, project_id, uuid4())
        path = settings.storage_root / key
        originals.append((path, content, path.stat()))
    staged = storage.stage(io.BytesIO(ARTIFACT))
    job_id = uuid4()
    key = storage.promote_render(staged, project_id, job_id)
    assert key == f"renders/{project_id}/{job_id}/final.mp4"
    path = settings.storage_root / key
    assert not staged.path.exists()
    before = path.stat()
    assert stat.S_IMODE(before.st_mode) == 0o400
    for directory in (path.parent, path.parent.parent, path.parent.parent.parent):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    opened = storage.open_render(key, project_id, job_id, len(ARTIFACT))
    with opened.file as stream:
        assert hashlib.sha256(stream.read()).hexdigest() == staged.checksum_sha256
    replacement = storage.stage(io.BytesIO(b"different-render"))
    with pytest.raises(AppError) as error:
        storage.promote_render(replacement, project_id, job_id)
    assert error.value.code == "render_publication_interrupted"
    assert replacement.path.exists()
    assert path.read_bytes() == ARTIFACT
    after = path.stat()
    assert (after.st_ino, after.st_mtime_ns, after.st_size, after.st_mode) == (
        before.st_ino,
        before.st_mtime_ns,
        before.st_size,
        before.st_mode,
    )
    for original, content, metadata in originals:
        current = original.stat()
        assert original.read_bytes() == content
        assert (current.st_ino, current.st_mtime_ns, current.st_mode) == (
            metadata.st_ino,
            metadata.st_mtime_ns,
            metadata.st_mode,
        )


@pytest.mark.parametrize(
    "key",
    [
        "../../etc/passwd",
        "/etc/passwd",
        "renders/../final.mp4",
        "renders\\project\\job\\final.mp4",
        "renders//project/job/final.mp4",
        "previews/project/job/preview.mp4",
        "originals/project/asset/source.mp4",
        "renders/project/job/final.mp4\x00",
    ],
)
def test_render_reader_rejects_untrusted_or_wrong_namespace_keys(render_storage, key):
    storage, project_id, job_id, correct_key, checksum = render_storage
    with pytest.raises(AppError) as error:
        storage.open_render(key, project_id, job_id, len(ARTIFACT))
    assert_unavailable(error)
    assert hashlib.sha256((storage.root / correct_key).read_bytes()).hexdigest() == checksum


@pytest.mark.parametrize("association", ["project", "job", "preview_reader"])
def test_render_reader_requires_exact_association(render_storage, association):
    storage, project_id, job_id, key, _checksum = render_storage
    with pytest.raises(AppError) as error:
        if association == "preview_reader":
            storage.open_preview(key, project_id, job_id, len(ARTIFACT))
        else:
            storage.open_render(
                key,
                uuid4() if association == "project" else project_id,
                uuid4() if association == "job" else job_id,
                len(ARTIFACT),
            )
    assert_unavailable(error)


@pytest.mark.parametrize("level", ["file", "job", "project", "renders", "root"])
def test_render_reader_rejects_symlinks_at_each_managed_level(render_storage, tmp_path, level):
    storage, project_id, job_id, key, _checksum = render_storage
    path = storage.root / key
    if level == "root":
        alias = tmp_path / "storage-alias"
        alias.symlink_to(storage.root, target_is_directory=True)
        storage.settings.storage_root = alias
        storage = LocalStorage(storage.settings)
    else:
        target = {
            "file": path,
            "job": path.parent,
            "project": path.parent.parent,
            "renders": path.parent.parent.parent,
        }[level]
        preserved = target.with_name(f"{target.name}-preserved")
        target.rename(preserved)
        target.symlink_to(preserved, target_is_directory=preserved.is_dir())
    with pytest.raises(AppError) as error:
        storage.open_render(key, project_id, job_id, len(ARTIFACT))
    assert_unavailable(error)


@pytest.mark.parametrize("byte_size", [-1, 0, len(ARTIFACT) - 1, len(ARTIFACT) + 1])
def test_render_reader_rejects_nonpositive_or_incorrect_size(render_storage, byte_size):
    storage, project_id, job_id, key, _checksum = render_storage
    with pytest.raises(AppError) as error:
        storage.open_render(key, project_id, job_id, byte_size)
    assert_unavailable(error)


def test_render_reader_reports_missing_or_inaccessible_file_safely(render_storage, monkeypatch):
    storage, project_id, job_id, key, _checksum = render_storage
    actual_open = os.open

    def denied(path, *args, **kwargs):
        if path == "final.mp4":
            raise PermissionError("/private/secret/final.mp4")
        return actual_open(path, *args, **kwargs)

    monkeypatch.setattr("app.storage.local.os.open", denied)
    with pytest.raises(AppError) as inaccessible:
        storage.open_render(key, project_id, job_id, len(ARTIFACT))
    assert_unavailable(inaccessible)
    monkeypatch.setattr("app.storage.local.os.open", actual_open)
    own_path = storage.root / key
    own_path.rename(own_path.with_name("preserved.mp4"))
    with pytest.raises(AppError) as missing:
        storage.open_render(key, project_id, job_id, len(ARTIFACT))
    assert_unavailable(missing)


@pytest.mark.parametrize("level", ["namespace", "project", "staging"])
def test_render_publication_rejects_symlink_destinations_and_staging(settings, tmp_path, level):
    settings.storage_min_free_bytes = 0
    storage = LocalStorage(settings)
    staged = storage.stage(io.BytesIO(ARTIFACT))
    project_id, job_id = uuid4(), uuid4()
    outside = tmp_path / "outside"
    outside.mkdir()
    source = outside / "preserved-original.mp4"
    source.write_bytes(b"preserve-this-source")
    before = source.stat()
    if level == "namespace":
        (storage.root / "renders").symlink_to(outside, target_is_directory=True)
    elif level == "project":
        (storage.root / "renders").mkdir()
        (storage.root / "renders" / str(project_id)).symlink_to(outside, target_is_directory=True)
    else:
        alias = storage.prepare_temp() / "unsafe-staging.mp4"
        alias.symlink_to(source)
        staged = StagedFile(alias, len(b"preserve-this-source"), "a" * 64)
    with pytest.raises(AppError) as error:
        storage.promote_render(staged, project_id, job_id)
    assert_unavailable(error)
    assert source.read_bytes() == b"preserve-this-source"
    after = source.stat()
    assert (before.st_mode, before.st_mtime_ns) == (after.st_mode, after.st_mtime_ns)
    assert not (outside / str(job_id)).exists()


def test_render_publication_refuses_disk_exhaustion_without_deleting_staging(settings, monkeypatch):
    settings.storage_min_free_bytes = 0
    storage = LocalStorage(settings)
    staged = storage.stage(io.BytesIO(ARTIFACT))
    project_id, job_id = uuid4(), uuid4()
    monkeypatch.setattr(
        "app.storage.local.shutil.disk_usage", lambda _path: type("Usage", (), {"free": 0})()
    )
    with pytest.raises(AppError) as error:
        storage.promote_render(staged, project_id, job_id)
    assert_unavailable(error)
    assert staged.path.read_bytes() == ARTIFACT
    assert not (storage.root / "renders").exists()


def test_render_publication_preserves_canonical_after_uncertain_fsync(settings, monkeypatch):
    settings.storage_min_free_bytes = 0
    storage = LocalStorage(settings)
    staged = storage.stage(io.BytesIO(ARTIFACT))
    project_id, job_id = uuid4(), uuid4()
    real_fsync = os.fsync

    def interrupted_after_link(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("/private/publication/fsync-failed")
        return real_fsync(fd)

    monkeypatch.setattr("app.storage.local.os.fsync", interrupted_after_link)
    with pytest.raises(AppError) as error:
        storage.promote_render(staged, project_id, job_id)
    assert_unavailable(error)
    target = storage.root / f"renders/{project_id}/{job_id}/final.mp4"
    assert target.read_bytes() == ARTIFACT
    assert stat.S_IMODE(target.stat().st_mode) == 0o400
    assert staged.path.exists()
    assert staged.path.stat().st_ino == target.stat().st_ino


def make_response(render_storage, *, disposition="attachment", filename=FILENAME):
    storage, project_id, job_id, key, checksum = render_storage
    opened = storage.open_render(key, project_id, job_id, len(ARTIFACT))
    return PrivateMediaResponse(opened, checksum, filename=filename, disposition=disposition)


async def idle_receive():
    await asyncio.Event().wait()


def collect_response(response, method="GET", headers=()):
    messages = []
    scope = {
        "type": "http",
        "method": method,
        "headers": list(headers),
        "state": {"request_id": "render-storage-unit"},
        "asgi": {"spec_version": "2.4"},
        "extensions": {"http.response.pathsend": {}},
    }

    async def send(message):
        messages.append(message)

    asyncio.run(response(scope, idle_receive, send))
    start = next(message for message in messages if message["type"] == "http.response.start")
    public_headers = {key.decode(): value.decode() for key, value in start["headers"]}
    bodies = [
        message.get("body", b"") for message in messages if message["type"] == "http.response.body"
    ]
    assert not any(message["type"] == "http.response.pathsend" for message in messages)
    assert all(len(body) <= 64 * 1024 for body in bodies)
    assert response.original.file.closed
    return start["status"], public_headers, b"".join(bodies)


@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize(
    "value,code,first,last",
    [
        (None, 200, 0, len(ARTIFACT)),
        ("bytes=0-15", 206, 0, 16),
        ("bytes=65531-65542", 206, 65531, 65543),
        (f"bytes={len(ARTIFACT) - 1}-", 206, len(ARTIFACT) - 1, len(ARTIFACT)),
        ("bytes=65536-", 206, 65536, len(ARTIFACT)),
        ("bytes=-31", 206, len(ARTIFACT) - 31, len(ARTIFACT)),
        ("bytes=17-17", 206, 17, 18),
    ],
)
def test_render_attachment_range_headers_bytes_and_descriptor_closure(
    render_storage, method, value, code, first, last
):
    response = make_response(render_storage)
    headers = [] if value is None else [(b"range", value.encode())]
    status_code, public_headers, body = collect_response(response, method, headers)
    assert status_code == code
    assert public_headers["content-disposition"] == f'attachment; filename="{FILENAME}"'
    assert public_headers["content-type"] == "video/mp4"
    assert public_headers["cache-control"] == "private, no-store"
    assert public_headers["x-content-type-options"] == "nosniff"
    assert public_headers["accept-ranges"] == "bytes"
    assert int(public_headers["content-length"]) == last - first
    assert body == (b"" if method == "HEAD" else ARTIFACT[first:last])
    if code == 206:
        assert public_headers["content-range"] == f"bytes {first}-{last - 1}/{len(ARTIFACT)}"
    else:
        assert "content-range" not in public_headers


@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("value", ["bad", "bytes=0-1,3-4", "bytes=-0", f"bytes={len(ARTIFACT)}-"])
def test_render_attachment_range_416_is_sanitized_and_closes_descriptor(
    render_storage, method, value
):
    response = make_response(render_storage)
    status_code, headers, body = collect_response(response, method, [(b"range", value.encode())])
    assert status_code == 416
    assert headers["content-range"] == f"bytes */{len(ARTIFACT)}"
    assert headers["content-disposition"] == f'attachment; filename="{FILENAME}"'
    assert headers["cache-control"] == "private, no-store"
    assert headers["x-content-type-options"] == "nosniff"
    assert body == b"" if method == "HEAD" else b"range_not_satisfiable" in body
    assert b"/private" not in body


@pytest.mark.parametrize(
    "filename,disposition",
    [
        ("../../private.mp4", "attachment"),
        ("edivo-r8-9x16-standard.mp4\r\nX-Private: yes", "attachment"),
        ("untrusted-original.mp4", "attachment"),
        ("edivo-r0-9x16-standard.mp4", "attachment"),
        ("edivo-r8-4x5-arbitrary.mp4", "attachment"),
        (None, "attachment"),
        (FILENAME, "inline"),
        (FILENAME, "arbitrary"),
    ],
)
def test_render_download_filename_accepts_only_server_generated_tokens(
    render_storage, filename, disposition
):
    storage, project_id, job_id, key, checksum = render_storage
    opened = storage.open_render(key, project_id, job_id, len(ARTIFACT))
    with opened.file, pytest.raises(ValueError):
        PrivateMediaResponse(opened, checksum, filename=filename, disposition=disposition)


def test_private_response_r04_default_remains_inline(render_storage):
    response = make_response(render_storage, disposition="inline", filename=None)
    status_code, headers, body = collect_response(response)
    assert status_code == 200
    assert headers["content-disposition"] == "inline"
    assert body == ARTIFACT


def test_render_attachment_descriptor_remains_pinned_after_path_swap(render_storage, tmp_path):
    response = make_response(render_storage)
    storage, _project_id, _job_id, key, _checksum = render_storage
    canonical = storage.root / key
    canonical.rename(canonical.with_name("preserved-final.mp4"))
    unrelated = tmp_path / "private.txt"
    unrelated.write_bytes(b"unrelated-private-content")
    canonical.symlink_to(unrelated)
    status_code, _headers, body = collect_response(response)
    assert status_code == 200
    assert body == ARTIFACT


def test_render_attachment_disconnect_closes_descriptor(render_storage):
    response = make_response(render_storage)

    async def send(message):
        if message["type"] == "http.response.body":
            raise OSError("synthetic-client-disconnect")

    async def exercise():
        with pytest.raises(OSError, match="synthetic-client-disconnect"):
            await response(
                {"type": "http", "method": "GET", "headers": [], "asgi": {"spec_version": "2.4"}},
                idle_receive,
                send,
            )

    asyncio.run(exercise())
    assert response.original.file.closed
