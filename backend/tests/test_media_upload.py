import asyncio
import copy
import hashlib
import json
import struct
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect, Request

from app.api.v1.media import SingleFileParser
from app.core.errors import AppError
from app.core.upload_limits import UploadBodyExceeded, UploadBodyLimitMiddleware
from app.main import create_app
from app.models.media_asset import MediaAsset
from app.repositories.projects import ProjectRepository
from app.storage.local import LocalStorage


def mp4_box(kind, payload=b""):
    return struct.pack(">I4s", len(payload) + 8, kind) + payload


MP4_BYTES = (
    mp4_box(b"ftyp", b"isom" + b"\0" * 4 + b"mp42")
    + mp4_box(b"moov")
    + mp4_box(b"mdat", b"synthetic-test-payload")
)
PROBE = {
    "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "1.0"},
    "streams": [
        {
            "index": 0,
            "codec_type": "video",
            "codec_name": "h264",
            "width": 320,
            "height": 180,
            "avg_frame_rate": "30000/1001",
            "duration": "1.0",
            "disposition": {"default": 1, "attached_pic": 0},
        },
    ],
    "frames": [{"media_type": "video", "stream_index": 0}],
}


@pytest.fixture
def synthetic_probe(monkeypatch):
    """HTTP/domain isolation only; real MP4/FFprobe is exercised by Compose acceptance."""
    payload = copy.deepcopy(PROBE)
    monkeypatch.setattr(
        "app.integrations.ffmpeg.probe.run_bounded",
        lambda *args: (0, json.dumps(payload).encode()),
    )
    return payload


@pytest.fixture
def project_id(domain_client):
    response = domain_client.post("/api/v1/projects", json={"name": "Upload test"})
    assert response.status_code == 201
    return response.json()["id"]


def upload(client, project_id, data=MP4_BYTES, filename="sample.mp4", mime="video/mp4"):
    return client.post(
        f"/api/v1/projects/{project_id}/media", files={"file": (filename, data, mime)}
    )


def count_assets(sessions):
    with sessions() as session:
        return session.scalar(select(func.count()).select_from(MediaAsset))


def assert_clean(settings, sessions):
    assert count_assets(sessions) == 0
    assert not list(settings.storage_root.rglob("source.mp4"))
    assert not list((settings.storage_root / "temp").glob("upload-*"))


@pytest.mark.parametrize("with_audio", [False, True])
def test_success_media_metadata_hash_immutable_and_paginated(
    domain_client, project_id, synthetic_probe, settings, domain_sessions, with_audio
):
    if with_audio:
        synthetic_probe["streams"].append({"index": 1, "codec_type": "audio", "codec_name": "aac"})
    response = upload(
        domain_client, project_id, filename="../../private/sample.MP4", mime="text/plain"
    )
    assert response.status_code == 201, response.text
    asset = response.json()
    assert asset["original_filename"] == "sample.MP4"
    assert asset["checksum_sha256"] == hashlib.sha256(MP4_BYTES).hexdigest()
    assert asset["byte_size"] == len(MP4_BYTES)
    assert asset["duration_ms"] == 1000 and (asset["width"], asset["height"]) == (320, 180)
    assert (asset["fps_num"], asset["fps_den"]) == (30000, 1001)
    assert asset["has_audio"] is with_audio
    assert asset["audio_codec"] == ("aac" if with_audio else None)
    assert "storage_key" not in asset and "probe_metadata" not in asset
    with domain_sessions() as session:
        persisted = session.get(MediaAsset, UUID(asset["id"]))
        original = settings.storage_root / persisted.storage_key
        assert original.read_bytes() == MP4_BYTES
        assert original.stat().st_mode & 0o777 == 0o400
    second = upload(domain_client, project_id)
    assert second.status_code == 201 and second.json()["id"] != asset["id"]
    assert original.read_bytes() == MP4_BYTES
    page = domain_client.get(f"/api/v1/projects/{project_id}/media?limit=1&offset=0").json()
    assert page["total"] == 2 and len(page["items"]) == 1
    assert page["items"][0]["id"] == second.json()["id"]
    detail = domain_client.get(f"/api/v1/projects/{project_id}/media/{asset['id']}")
    assert detail.status_code == 200 and detail.json() == asset
    assert list((settings.storage_root / "temp").iterdir()) == []
    domain_client.delete(f"/api/v1/projects/{project_id}")
    assert original.read_bytes() == MP4_BYTES
    assert domain_client.get(f"/api/v1/projects/{project_id}/media").status_code == 404
    assert (
        domain_client.get(f"/api/v1/projects/{project_id}/media/{asset['id']}").status_code == 404
    )
    assert upload(domain_client, project_id).status_code == 404


@pytest.mark.parametrize(
    "data,name,status",
    [
        (b"", "empty.mp4", 422),
        (MP4_BYTES, "wrong.mov", 415),
        (MP4_BYTES, "wrong.mp4evil", 415),
        (MP4_BYTES, "...", 415),
        (b"this is text", "fake.mp4", 422),
        (mp4_box(b"free", b"ftypisom"), "fake.mp4", 415),
        (MP4_BYTES[:-3], "corrupt.mp4", 422),
    ],
)
def test_negative_upload_cleans_all_artifacts(
    domain_client, project_id, synthetic_probe, settings, domain_sessions, data, name, status
):
    response = upload(domain_client, project_id, data=data, filename=name)
    assert response.status_code == status, response.text
    assert set(response.json()) == {"code", "message", "request_id"}
    assert_clean(settings, domain_sessions)


@pytest.mark.parametrize("mode,status", [("no_video", 422), ("long", 422), ("bad_output", 422)])
def test_content_validation_cleans_artifacts(
    domain_client, project_id, synthetic_probe, settings, domain_sessions, monkeypatch, mode, status
):
    if mode == "no_video":
        synthetic_probe["streams"] = []
    elif mode == "long":
        synthetic_probe["format"]["duration"] = "600.001"
    else:
        monkeypatch.setattr("app.integrations.ffmpeg.probe.run_bounded", lambda *args: (0, b"bad"))
    response = upload(domain_client, project_id)
    assert response.status_code == status
    assert_clean(settings, domain_sessions)


@pytest.mark.parametrize("code", ["probe_unavailable", "probe_timeout"])
def test_probe_dependency_failure_sanitized(
    domain_client, project_id, settings, domain_sessions, monkeypatch, code
):
    def fail(*_args):
        raise AppError(503, code, "Análise temporariamente indisponível.")

    monkeypatch.setattr("app.integrations.ffmpeg.probe.run_bounded", fail)
    response = upload(domain_client, project_id)
    assert response.status_code == 503 and response.json()["code"] == code
    assert_clean(settings, domain_sessions)


@pytest.mark.parametrize(
    "files,data",
    [
        ([("file", ("a.mp4", MP4_BYTES)), ("file", ("b.mp4", MP4_BYTES))], None),
        ({"another": ("a.mp4", MP4_BYTES)}, None),
        ({"file": ("a.mp4", MP4_BYTES)}, {"extra": "forbidden"}),
    ],
)
def test_multipart_restricts_files_names_and_fields(
    domain_client, project_id, settings, domain_sessions, files, data
):
    response = domain_client.post(f"/api/v1/projects/{project_id}/media", files=files, data=data)
    assert response.status_code == 400, response.text
    assert_clean(settings, domain_sessions)


def multipart_body(data=MP4_BYTES, filename="sample.mp4", boundary="edivo-test", close=True):
    return (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{filename}"\r\n'
            "Content-Type: video/mp4\r\n\r\n"
        ).encode()
        + data
        + (f"\r\n--{boundary}--\r\n".encode() if close else b"")
    )


@pytest.mark.parametrize(
    "mode", ["missing_boundary", "incomplete", "huge_headers", "huge_boundary"]
)
def test_malformed_multipart_is_bounded_and_clean(
    domain_client, project_id, settings, domain_sessions, mode
):
    boundary = "edivo-test" if mode != "huge_boundary" else "a" * 201
    body = multipart_body(
        filename="a" * 9000 + ".mp4" if mode == "huge_headers" else "x.mp4",
        close=mode != "incomplete",
        boundary=boundary,
    )
    content_type = "multipart/form-data" + (
        f"; boundary={boundary}" if mode != "missing_boundary" else ""
    )
    response = domain_client.post(
        f"/api/v1/projects/{project_id}/media", content=body, headers={"Content-Type": content_type}
    )
    assert response.status_code == 400, response.text
    assert_clean(settings, domain_sessions)


def test_malformed_multipart_parser_exception_is_400(
    domain_client, project_id, settings, domain_sessions
):
    response = domain_client.post(
        f"/api/v1/projects/{project_id}/media",
        content=b"this-is-not-the-declared-boundary",
        headers={"Content-Type": "multipart/form-data; boundary=edivo-test"},
    )
    assert response.status_code == 400
    assert_clean(settings, domain_sessions)


def test_file_limit_during_parser_without_content_length(
    domain_client, project_id, settings, domain_sessions
):
    settings.upload_max_bytes = 20
    body = multipart_body(data=b"x" * 100)
    response = domain_client.post(
        f"/api/v1/projects/{project_id}/media",
        content=iter([body[:50], body[50:]]),
        headers={"Content-Type": "multipart/form-data; boundary=edivo-test"},
    )
    assert response.status_code == 413, response.text
    assert_clean(settings, domain_sessions)


@pytest.mark.parametrize("content_length", [None, "1", "-1", "invalid", "999999"])
def test_ingress_body_bound_before_parser(settings, content_length):
    seen = []
    outputs = []
    messages = iter(
        [
            {"type": "http.request", "body": b"x" * 8, "more_body": True},
            {"type": "http.request", "body": b"y" * 8, "more_body": False},
        ]
    )

    async def app(scope, receive, send):
        while True:
            message = await receive()
            seen.append(message)
            if not message.get("more_body"):
                break

    async def receive():
        return next(messages)

    async def send(message):
        outputs.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": f"/api/v1/projects/{uuid4()}/media",
        "headers": [] if content_length is None else [(b"content-length", content_length.encode())],
    }
    middleware = UploadBodyLimitMiddleware(app, max_body_bytes=10)
    if content_length in (None, "1"):
        with pytest.raises(UploadBodyExceeded):
            asyncio.run(middleware(scope, receive, send))
        assert len(seen) == 1
    else:
        asyncio.run(middleware(scope, receive, send))
        assert not seen
        assert outputs[0]["status"] == (413 if content_length == "999999" else 400)


def test_body_limit_http_preparser_allows_multipart_overhead(
    settings, domain_sessions, synthetic_probe
):
    settings.upload_max_bytes = len(MP4_BYTES)
    settings.upload_multipart_overhead_bytes = 1024
    app = create_app(settings)
    app.state.database.sessions = domain_sessions
    with TestClient(app, raise_server_exceptions=False) as client:
        project = client.post("/api/v1/projects", json={"name": "limit"}).json()["id"]
        response = upload(client, project)
        assert response.status_code == 201, response.text
        response = client.post(
            f"/api/v1/projects/{project}/media",
            content=b"x" * (len(MP4_BYTES) + 1025),
            headers={"Content-Type": "multipart/form-data; boundary=edivo-test"},
        )
        assert response.status_code == 413


def test_database_flush_failure_never_publishes(
    domain_client, project_id, synthetic_probe, domain_sessions, settings, monkeypatch
):
    def fail(*args, **kwargs):
        raise SQLAlchemyError("postgresql://private:secret@private")

    monkeypatch.setattr(Session, "flush", fail)
    response = upload(domain_client, project_id)
    assert response.status_code == 503 and "private" not in response.text
    monkeypatch.undo()
    assert_clean(settings, domain_sessions)


def test_commit_failure_compensates_uncommitted_file(
    domain_client, project_id, synthetic_probe, settings, domain_sessions, monkeypatch
):
    def fail(*args, **kwargs):
        raise SQLAlchemyError("secret database error")

    monkeypatch.setattr(Session, "commit", fail)
    response = upload(domain_client, project_id)
    assert response.status_code == 503 and "secret" not in response.text
    assert_clean(settings, domain_sessions)


def test_commit_acknowledgement_failure_preserves_committed_original(
    domain_client, project_id, synthetic_probe, settings, domain_sessions, monkeypatch
):
    real_commit = Session.commit

    def commit_then_disconnect(session):
        real_commit(session)
        raise SQLAlchemyError("lost commit response")

    monkeypatch.setattr(Session, "commit", commit_then_disconnect)
    response = upload(domain_client, project_id)
    assert response.status_code == 503
    assert count_assets(domain_sessions) == 1
    assert len(list(settings.storage_root.rglob("source.mp4"))) == 1
    assert list((settings.storage_root / "temp").iterdir()) == []


def test_commit_uncertain_and_database_offline_preserves_original_for_review(
    domain_client, project_id, synthetic_probe, settings, domain_sessions, monkeypatch
):
    real_scalar = Session.scalar
    commit_attempted = False

    def fail_commit(session):
        nonlocal commit_attempted
        commit_attempted = True
        raise SQLAlchemyError("lost database connection")

    def scalar_offline_after_commit(session, *args, **kwargs):
        if commit_attempted:
            raise SQLAlchemyError("verification unavailable")
        return real_scalar(session, *args, **kwargs)

    monkeypatch.setattr(Session, "commit", fail_commit)
    monkeypatch.setattr(Session, "scalar", scalar_offline_after_commit)
    response = upload(domain_client, project_id)
    assert response.status_code == 503
    assert len(list(settings.storage_root.rglob("source.mp4"))) == 1
    assert list((settings.storage_root / "temp").iterdir()) == []
    monkeypatch.undo()
    assert count_assets(domain_sessions) == 0


def test_storage_failure_sanitized_and_clean(
    domain_client, project_id, synthetic_probe, settings, domain_sessions, monkeypatch
):
    def fail(*args, **kwargs):
        raise OSError("/private/path permission denied")

    monkeypatch.setattr(LocalStorage, "promote", fail)
    response = upload(domain_client, project_id)
    assert response.status_code == 503 and "private" not in response.text
    assert_clean(settings, domain_sessions)


def test_project_revalidated_under_lock_after_probe(
    domain_client, project_id, synthetic_probe, settings, domain_sessions, monkeypatch
):
    original = ProjectRepository.get_active

    def deleted_during_upload(repo, identifier, *, for_update=False):
        return None if for_update else original(repo, identifier)

    monkeypatch.setattr(ProjectRepository, "get_active", deleted_during_upload)
    response = upload(domain_client, project_id)
    assert response.status_code == 404
    assert_clean(settings, domain_sessions)


def test_missing_project_and_wrong_media_relation(domain_client, project_id, synthetic_probe):
    assert upload(domain_client, uuid4()).status_code == 404
    asset = upload(domain_client, project_id).json()
    other_project = domain_client.post("/api/v1/projects", json={"name": "other"}).json()["id"]
    response = domain_client.get(f"/api/v1/projects/{other_project}/media/{asset['id']}")
    assert response.status_code == 404
    assert domain_client.get(f"/api/v1/projects/{project_id}/media/{uuid4()}").status_code == 404
    assert domain_client.get(f"/api/v1/projects/{project_id}/media?limit=101").status_code == 422


def test_parser_spools_in_managed_storage_and_cleans_disconnect(settings):
    body = multipart_body(data=b"x" * (2 * 1024 * 1024), close=False)
    events = iter(
        [
            {"type": "http.request", "body": body, "more_body": True},
            {"type": "http.disconnect"},
        ]
    )

    async def receive():
        return next(events)

    scope = {
        "type": "http",
        "headers": [(b"content-type", b"multipart/form-data; boundary=edivo-test")],
    }
    parser = SingleFileParser(Request(scope, receive), settings)
    with pytest.raises(ClientDisconnect):
        asyncio.run(parser.parse())
    assert parser._files_to_close_on_error and all(
        s.closed for s in parser._files_to_close_on_error
    )
    assert list((settings.storage_root / "temp").iterdir()) == []
