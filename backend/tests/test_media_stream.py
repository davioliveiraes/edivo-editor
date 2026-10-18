"""Descriptor/HTTP contract tests; real MP4 playback is a separate Compose/browser gate."""

import asyncio
import hashlib
import io
import os
import stat
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.api.media_stream import PrivateMediaResponse
from app.api.v1 import media as media_api
from app.models.media_asset import MediaAsset
from app.models.project import Project
from app.storage.local import LocalStorage

SOURCE_BYTES = bytes(range(256)) * 701


@pytest.fixture
def stream_source(settings, domain_sessions):
    project_id, asset_id = uuid4(), uuid4()
    storage = LocalStorage(settings)
    staged = storage.stage(io.BytesIO(SOURCE_BYTES))
    key = storage.promote(staged, project_id, asset_id)
    with domain_sessions() as session:
        session.add(Project(id=project_id, name="Private stream"))
        session.flush()
        session.add(
            MediaAsset(
                id=asset_id,
                project_id=project_id,
                original_filename='untrusted"name.mp4',
                storage_key=key,
                checksum_sha256=staged.checksum_sha256,
                byte_size=staged.byte_size,
                mime_detected="video/mp4",
                duration_ms=1000,
                width=320,
                height=180,
                fps_num=30,
                fps_den=1,
                video_codec="h264",
                has_audio=False,
                audio_codec=None,
                probe_metadata={},
            )
        )
        session.commit()
    return {
        "project_id": project_id,
        "media_id": asset_id,
        "key": key,
        "path": settings.storage_root / key,
        "url": f"/api/v1/projects/{project_id}/media/{asset_id}/stream",
        "checksum": staged.checksum_sha256,
    }


def assert_private_headers(response):
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-disposition"] == "inline"


def assert_sanitized(response, status, settings):
    assert response.status_code == status, response.text
    assert set(response.json()) == {"code", "message", "request_id"}
    assert response.json()["request_id"] == response.headers["x-request-id"]
    assert str(settings.storage_root) not in response.text
    assert "source.mp4" not in response.text
    assert "Traceback" not in response.text


def test_stream_complete_and_original_integrity(domain_client, stream_source):
    path = stream_source["path"]
    before = path.stat()
    response = domain_client.get(stream_source["url"])
    assert response.status_code == 200
    assert response.content == SOURCE_BYTES
    assert int(response.headers["content-length"]) == len(SOURCE_BYTES)
    assert response.headers["content-type"] == "video/mp4"
    assert "content-range" not in response.headers
    assert response.headers["etag"] == f'"sha256:{stream_source["checksum"]}"'
    assert_private_headers(response)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == stream_source["checksum"]
    after = path.stat()
    assert stat.S_IMODE(after.st_mode) == stat.S_IMODE(before.st_mode) == 0o400
    assert after.st_size == before.st_size == len(SOURCE_BYTES)
    assert after.st_mtime_ns == before.st_mtime_ns


@pytest.mark.parametrize(
    "value,start,end",
    [
        ("bytes=0-15", 0, 16),
        ("bytes=512-1023", 512, 1024),
        (
            f"bytes={len(SOURCE_BYTES) - 16}-{len(SOURCE_BYTES) - 1}",
            len(SOURCE_BYTES) - 16,
            len(SOURCE_BYTES),
        ),
        ("bytes=65536-", 65536, len(SOURCE_BYTES)),
        ("bytes=-31", len(SOURCE_BYTES) - 31, len(SOURCE_BYTES)),
        ("bytes=17-17", 17, 18),
        ("bytes=0-0", 0, 1),
        (f"bytes={len(SOURCE_BYTES) - 1}-", len(SOURCE_BYTES) - 1, len(SOURCE_BYTES)),
        ("bytes=0-999999999999999", 0, len(SOURCE_BYTES)),
        ("bytes=-999999999999999", 0, len(SOURCE_BYTES)),
        ("BYTES=1-2", 1, 3),
        (" bytes=1-2 ", 1, 3),
    ],
)
def test_single_ranges_exact_bytes(domain_client, stream_source, value, start, end):
    response = domain_client.get(stream_source["url"], headers={"Range": value.encode("utf-8")})
    assert response.status_code == 206, response.text
    assert response.content == SOURCE_BYTES[start:end]
    assert int(response.headers["content-length"]) == end - start
    assert response.headers["content-range"] == f"bytes {start}-{end - 1}/{len(SOURCE_BYTES)}"
    assert response.headers["content-type"] == "video/mp4"
    assert_private_headers(response)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "bytes=",
        "bytes=-",
        "bytes=-0",
        "bytes=3-2",
        "bytes=abc-def",
        "bytes=0-1,3-4",
        "bytes=0-1,0-0",
        "bytes=0-1,",
        "bytes=+1-2",
        "bytes=-1-2",
        "bytes=1.0-2",
        "bytes=١-٢",
        "bytes=1 - 2",
        "items=0-1",
        "bytes:0-1",
        f"bytes={len(SOURCE_BYTES)}-",
        "bytes=999999999999999999999-",
        "bytes=" + "0" * 260 + "-1",
    ],
)
def test_invalid_unsatisfiable_multiple_ranges_are_416(
    domain_client, stream_source, value, settings
):
    response = domain_client.get(stream_source["url"], headers={"Range": value.encode("utf-8")})
    assert_sanitized(response, 416, settings)
    assert response.json()["code"] == "range_not_satisfiable"
    assert response.headers["content-range"] == f"bytes */{len(SOURCE_BYTES)}"
    assert int(response.headers["content-length"]) == len(response.content)
    assert_private_headers(response)


def test_repeated_range_headers_are_rejected(domain_client, stream_source, settings):
    response = domain_client.get(
        stream_source["url"], headers=[("Range", "bytes=0-1"), ("Range", "bytes=3-4")]
    )
    assert_sanitized(response, 416, settings)


@pytest.mark.parametrize("value,status", [(None, 200), ("bytes=30-50", 206), ("bytes=-0", 416)])
def test_head_matches_get_headers_without_body(domain_client, stream_source, value, status):
    headers = {"Range": value} if value is not None else {}
    get = domain_client.get(stream_source["url"], headers=headers)
    head = domain_client.head(stream_source["url"], headers=headers)
    assert head.status_code == get.status_code == status
    assert head.content == b""
    for name in (
        "content-length",
        "content-range",
        "content-type",
        "accept-ranges",
        "cache-control",
    ):
        assert head.headers.get(name) == get.headers.get(name)


@pytest.mark.parametrize("validator", ["etag", "last-modified", "mismatch", "weak"])
def test_if_range_uses_native_strong_or_date_validator(domain_client, stream_source, validator):
    headers = domain_client.head(stream_source["url"]).headers
    value = (
        headers[validator]
        if validator in ("etag", "last-modified")
        else '"other"'
        if validator == "mismatch"
        else "W/" + headers["etag"]
    )
    response = domain_client.get(
        stream_source["url"], headers={"Range": "bytes=1-4", "If-Range": value}
    )
    assert response.status_code == (206 if validator in ("etag", "last-modified") else 200)
    assert response.content == (SOURCE_BYTES[1:5] if response.status_code == 206 else SOURCE_BYTES)
    assert_private_headers(response)


def test_invalid_range_still_rejected_with_mismatching_if_range(domain_client, stream_source):
    response = domain_client.get(
        stream_source["url"], headers={"Range": "bytes=0-1,3-4", "If-Range": '"other"'}
    )
    assert response.status_code == 416


@pytest.mark.parametrize("method", ["get", "head"])
def test_project_and_media_authorization(domain_client, stream_source, settings, method):
    request = getattr(domain_client, method)
    another = domain_client.post("/api/v1/projects", json={"name": "Other project"}).json()["id"]
    wrong_project = f"/api/v1/projects/{another}/media/{stream_source['media_id']}/stream"
    missing_media = f"/api/v1/projects/{stream_source['project_id']}/media/{uuid4()}/stream"
    missing_project = f"/api/v1/projects/{uuid4()}/media/{stream_source['media_id']}/stream"
    for url in (wrong_project, missing_media, missing_project):
        response = request(url)
        assert response.status_code == 404
        assert str(settings.storage_root) not in response.text
    assert (
        domain_client.delete(f"/api/v1/projects/{stream_source['project_id']}").status_code == 204
    )
    assert request(stream_source["url"]).status_code == 404
    assert stream_source["path"].read_bytes() == SOURCE_BYTES


@pytest.mark.parametrize("method", ["get", "head"])
def test_missing_original_is_sanitized_503(domain_client, stream_source, settings, method):
    # Test-owned fixture only; existing project/storage data are not involved.
    stream_source["path"].unlink()
    response = getattr(domain_client, method)(stream_source["url"])
    assert response.status_code == 503
    if method == "get":
        assert_sanitized(response, 503, settings)


def test_permission_failure_is_sanitized(domain_client, stream_source, settings, monkeypatch):
    real_open = os.open

    def deny_original(path, *args, **kwargs):
        if path == "source.mp4":
            raise PermissionError("/private/secret source.mp4")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("app.storage.local.os.open", deny_original)
    response = domain_client.get(stream_source["url"])
    assert_sanitized(response, 503, settings)
    assert "secret" not in response.text


@pytest.mark.parametrize("target", ["leaf", "asset", "project", "originals", "root", "ancestor"])
def test_all_symlink_components_rejected(domain_client, stream_source, settings, tmp_path, target):
    original = stream_source["path"]
    if target == "leaf":
        unsafe = original
    elif target == "asset":
        unsafe = original.parent
    elif target == "project":
        unsafe = original.parent.parent
    elif target == "originals":
        unsafe = settings.storage_root / "originals"
    else:
        # Exercise root and a configured root ancestor without altering pytest's own root.
        actual_root = settings.storage_root
        alias = tmp_path / "alias"
        alias.symlink_to(actual_root, target_is_directory=True)
        settings.storage_root = alias if target == "root" else alias / "nested"
        response = domain_client.get(stream_source["url"])
        assert_sanitized(response, 503, settings)
        return
    renamed = unsafe.with_name(unsafe.name + "-preserved")
    unsafe.rename(renamed)
    unsafe.symlink_to(renamed, target_is_directory=renamed.is_dir())
    response = domain_client.get(stream_source["url"])
    assert_sanitized(response, 503, settings)


@pytest.mark.parametrize(
    "key",
    [
        "../../etc/passwd",
        "/etc/passwd",
        "originals/../source.mp4",
        "originals\\project\\source.mp4",
        "originals/fake/fake/source.mp4",
        "originals//project/source.mp4",
    ],
)
def test_persisted_malicious_key_is_not_opened(
    domain_client, stream_source, domain_sessions, settings, key
):
    with domain_sessions() as session:
        asset = session.get(MediaAsset, stream_source["media_id"])
        asset.storage_key = key
        session.commit()
    response = domain_client.get(stream_source["url"])
    assert_sanitized(response, 503, settings)
    assert "passwd" not in response.text
    assert stream_source["path"].read_bytes() == SOURCE_BYTES


def test_query_path_cannot_choose_a_file(domain_client, stream_source):
    response = domain_client.get(stream_source["url"], params={"path": "/etc/passwd"})
    assert response.status_code == 200
    assert response.content == SOURCE_BYTES


def test_configured_relative_parent_root_remains_compatible(
    domain_client, stream_source, settings, monkeypatch
):
    # R01's native default is ../storage; parent navigation belongs only to trusted
    # configuration, never to a persisted key or an HTTP-provided pathname.
    working = settings.storage_root / "backend-workdir"
    working.mkdir()
    monkeypatch.chdir(working)
    settings.storage_root = Path("..")
    response = domain_client.get(stream_source["url"])
    assert response.status_code == 200
    assert response.content == SOURCE_BYTES


@pytest.mark.parametrize("kind", ["directory", "fifo", "different_size"])
def test_nonregular_and_size_mismatch_rejected(domain_client, stream_source, settings, kind):
    path = stream_source["path"]
    path.unlink()
    if kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        path.write_bytes(b"short")
    response = domain_client.get(stream_source["url"])
    assert_sanitized(response, 503, settings)


def test_database_failure_is_sanitized(domain_client, stream_source, settings, monkeypatch):
    def fail(*_args, **_kwargs):
        raise SQLAlchemyError("postgresql://private:secret@db /private/path")

    monkeypatch.setattr("app.repositories.projects.ProjectRepository.get_active", fail)
    response = domain_client.get(stream_source["url"])
    assert_sanitized(response, 503, settings)
    assert "secret" not in response.text


def test_unauthorized_request_never_opens_storage(domain_client, stream_source, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("No storage descriptor may be opened before authorization")

    monkeypatch.setattr(LocalStorage, "open_original", forbidden)
    response = domain_client.get(
        f"/api/v1/projects/{uuid4()}/media/{stream_source['media_id']}/stream"
    )
    assert response.status_code == 404


def make_response(settings, stream_source):
    opened = LocalStorage(settings).open_original(
        stream_source["key"],
        stream_source["project_id"],
        stream_source["media_id"],
        len(SOURCE_BYTES),
    )
    return PrivateMediaResponse(opened, stream_source["checksum"])


def response_scope(method="GET", headers=None, spec="2.4"):
    return {
        "type": "http",
        "method": method,
        "headers": headers or [],
        "state": {"request_id": "stream-test"},
        "asgi": {"spec_version": spec},
        "extensions": {"http.response.pathsend": {}},
    }


async def receive_idle():
    await asyncio.sleep(3600)


@pytest.mark.parametrize(
    "method,headers", [("GET", []), ("HEAD", []), ("GET", [(b"range", b"bad")])]
)
def test_descriptor_closed_and_no_pathsend(settings, stream_source, method, headers):
    response = make_response(settings, stream_source)
    sent = []

    async def send(message):
        sent.append(message)

    asyncio.run(response(response_scope(method, headers), receive_idle, send))
    assert response.original.file.closed
    assert not any(message["type"] == "http.response.pathsend" for message in sent)
    chunks = [
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    ]
    assert all(len(chunk) <= 64 * 1024 for chunk in chunks)
    if method == "HEAD":
        assert chunks == [b""]
    elif not headers:
        assert b"".join(chunks) == SOURCE_BYTES
        assert len(chunks) >= 3


@pytest.mark.parametrize("spec", ["2.3", "2.4"])
def test_disconnect_closes_descriptor(settings, stream_source, spec):
    response = make_response(settings, stream_source)

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        if spec == "2.4" and message["type"] == "http.response.body":
            raise OSError("client disconnected")

    async def exercise():
        if spec == "2.4":
            with pytest.raises(OSError):
                await response(response_scope(spec=spec), receive, send)
        else:
            await response(response_scope(spec=spec), receive, send)

    asyncio.run(exercise())
    assert response.original.file.closed


def test_cancellation_before_body_open_closes_descriptor(settings, stream_source):
    response = make_response(settings, stream_source)

    async def send(_message):
        raise asyncio.CancelledError

    async def exercise():
        with pytest.raises(asyncio.CancelledError):
            await response(response_scope(), receive_idle, send)

    asyncio.run(exercise())
    assert response.original.file.closed


def test_pinned_descriptor_survives_path_swap(settings, stream_source, tmp_path):
    response = make_response(settings, stream_source)
    original = stream_source["path"]
    original.rename(original.with_name("preserved-original.mp4"))
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"private-host-content")
    original.symlink_to(secret)
    chunks = []

    async def send(message):
        if message["type"] == "http.response.body":
            chunks.append(message["body"])

    asyncio.run(response(response_scope(), receive_idle, send))
    assert b"".join(chunks) == SOURCE_BYTES
    assert response.original.file.closed


def test_response_construction_failure_closes_descriptor(
    domain_client, stream_source, settings, monkeypatch
):
    opened = []
    real_open = LocalStorage.open_original

    def tracked(*args, **kwargs):
        original = real_open(*args, **kwargs)
        opened.append(original)
        return original

    def broken(*_args, **_kwargs):
        raise RuntimeError("/private/secret")

    monkeypatch.setattr(LocalStorage, "open_original", tracked)
    monkeypatch.setattr("app.api.v1.media.PrivateMediaResponse", broken)
    response = domain_client.get(stream_source["url"])
    assert_sanitized(response, 500, settings)
    assert len(opened) == 1 and opened[0].file.closed


def test_stream_releases_database_transaction_before_body(
    domain_client, stream_source, domain_sessions, monkeypatch
):
    # The session remains request-scoped but the body must not hold its connection.
    real_response = PrivateMediaResponse.__call__
    sessions = []
    real_rollback = media_api.safe_rollback

    def tracked(session):
        real_rollback(session)
        sessions.append(session)

    async def inspect(response, scope, receive, send):
        assert sessions and not sessions[-1].in_transaction()
        await real_response(response, scope, receive, send)

    monkeypatch.setattr("app.api.v1.media.safe_rollback", tracked)
    monkeypatch.setattr(PrivateMediaResponse, "__call__", inspect)
    response = domain_client.get(stream_source["url"])
    assert response.status_code == 200
    with domain_sessions() as session:
        asset = session.scalar(
            select(MediaAsset).where(MediaAsset.id == UUID(str(stream_source["media_id"])))
        )
        assert asset.checksum_sha256 == stream_source["checksum"]
