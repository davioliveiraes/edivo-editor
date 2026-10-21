"""Bounded MP3 HTTP imports use isolated probe output; real FFprobe has separate QA."""

import asyncio
import hashlib
import io
import json
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect, Request
from test_media_upload import multipart_body

from app.api.v1.media import SingleFileParser
from app.core.errors import AppError
from app.core.upload_limits import UploadBodyExceeded, UploadBodyLimitMiddleware
from app.main import create_app
from app.models.audio_asset import AudioAsset
from app.repositories.projects import ProjectRepository
from app.storage.local import LocalStorage

MP3_BYTES = (b"\xff\xfb\x90\x00" + b"\x00" * 413) * 40
AUDIO_PROBE = {
    "format": {"format_name": "mp3", "duration": "1.045"},
    "streams": [
        {
            "index": 0,
            "codec_type": "audio",
            "codec_name": "mp3",
            "duration": "1.045",
            "sample_rate": "44100",
            "channels": 2,
        }
    ],
    "frames": [{"media_type": "audio", "stream_index": 0, "nb_samples": 1152}] * 40,
}


@pytest.fixture
def audio_probe(monkeypatch):
    monkeypatch.setattr(
        "app.integrations.ffmpeg.audio_probe.run_bounded",
        lambda *_args: (0, json.dumps(AUDIO_PROBE).encode()),
    )


@pytest.fixture
def project(domain_client):
    return domain_client.post("/api/v1/projects", json={"name": "Música"}).json()["id"]


def upload(client, project, data=MP3_BYTES, filename="Música.mp3"):
    return client.post(
        f"/api/v1/projects/{project}/audio", files={"file": (filename, data, "text/plain")}
    )


def assert_clean(settings, sessions):
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(AudioAsset)) == 0
    assert not list(settings.storage_root.rglob("source.mp3"))
    assert not list((settings.storage_root / "temp").glob("upload-*"))


def test_audio_private_immutable_inventory_pagination_soft_delete(
    domain_client, project, audio_probe, settings, domain_sessions
):
    response = upload(domain_client, project, filename="../../Música.MP3")
    assert response.status_code == 201, response.text
    asset = response.json()
    assert asset["original_filename"] == "Música.MP3"
    assert asset["checksum_sha256"] == hashlib.sha256(MP3_BYTES).hexdigest()
    assert asset["mime_detected"] == "audio/mpeg" and asset["codec"] == "mp3"
    assert asset["duration_ms"] == 1045 and asset["byte_size"] == len(MP3_BYTES)
    assert asset["sample_rate"] == 44100 and asset["channels"] == 2
    assert "storage_key" not in asset and "probe_metadata" not in asset
    with domain_sessions() as session:
        row = session.get(AudioAsset, UUID(asset["id"]))
        storage_key = row.storage_key
    path = settings.storage_root / storage_key
    assert path.read_bytes() == MP3_BYTES and path.stat().st_mode & 0o777 == 0o400
    opened = LocalStorage(settings).open_audio(
        storage_key, UUID(project), UUID(asset["id"]), len(MP3_BYTES)
    )
    assert opened.file.read() == MP3_BYTES
    opened.file.close()
    second = upload(domain_client, project)
    assert second.status_code == 201 and second.json()["id"] != asset["id"]
    page = domain_client.get(f"/api/v1/projects/{project}/audio?limit=1").json()
    assert page["total"] == 2 and page["items"][0]["id"] == second.json()["id"]
    assert domain_client.get(f"/api/v1/projects/{project}/audio/{asset['id']}").json() == asset
    assert domain_client.delete(f"/api/v1/projects/{project}").status_code == 204
    assert domain_client.get(f"/api/v1/projects/{project}/audio").status_code == 404
    assert domain_client.get(f"/api/v1/projects/{project}/audio/{asset['id']}").status_code == 404
    assert upload(domain_client, project).status_code == 404
    assert path.read_bytes() == MP3_BYTES


@pytest.mark.parametrize(
    "data,name,status",
    [
        (b"", "a.mp3", 422),
        (MP3_BYTES, "a.mp4", 415),
        (b"wrong file content", "a.mp3", 415),
        (MP3_BYTES[:-1], "a.mp3", 422),
    ],
)
def test_rejected_audio_leaves_no_rows_or_files(
    domain_client, project, audio_probe, settings, domain_sessions, data, name, status
):
    response = upload(domain_client, project, data, name)
    assert response.status_code == status, response.text
    assert set(response.json()) == {"code", "message", "request_id"}
    assert_clean(settings, domain_sessions)


@pytest.mark.parametrize(
    "files,data",
    [
        ([("file", ("a.mp3", MP3_BYTES)), ("file", ("b.mp3", MP3_BYTES))], None),
        ({"another": ("a.mp3", MP3_BYTES)}, None),
        ({"file": ("a.mp3", MP3_BYTES)}, {"extra": "x"}),
    ],
)
def test_audio_one_file_no_fields(domain_client, project, settings, domain_sessions, files, data):
    assert (
        domain_client.post(f"/api/v1/projects/{project}/audio", files=files, data=data).status_code
        == 400
    )
    assert_clean(settings, domain_sessions)


def test_audio_limit_independent_at_ingress_parser_and_staging(
    settings, domain_sessions, audio_probe
):
    settings.audio_upload_max_bytes = 100
    app = create_app(settings)
    app.state.database.sessions = domain_sessions
    with TestClient(app, raise_server_exceptions=False) as client:
        project = client.post("/api/v1/projects", json={"name": "Limites"}).json()["id"]
        # Chunked/no Content-Length: the multipart file parser catches its own 100-byte limit.
        body = multipart_body(data=b"x" * 101, filename="a.mp3")
        response = client.post(
            f"/api/v1/projects/{project}/audio",
            content=iter([body[:50], body[50:]]),
            headers={"Content-Type": "multipart/form-data; boundary=edivo-test"},
        )
        assert response.status_code == 413
        # Ingress rejects before parsing even when the MP4 default is 500 MiB.
        response = client.post(
            f"/api/v1/projects/{project}/audio",
            content=b"x" * (100 + settings.upload_multipart_overhead_bytes + 1),
        )
        assert response.status_code == 413
    with pytest.raises(AppError) as error:
        LocalStorage(settings).stage_audio(io.BytesIO(b"x" * 101))
    assert error.value.status_code == 413
    assert settings.upload_max_bytes == 500 * 1024 * 1024
    assert_clean(settings, domain_sessions)


def test_audio_ingress_without_length_has_independent_bound():
    messages = iter(
        [
            {"type": "http.request", "body": b"x" * 6, "more_body": True},
            {"type": "http.request", "body": b"y" * 6, "more_body": False},
        ]
    )
    seen = []

    async def app(scope, receive, send):
        while True:
            message = await receive()
            seen.append(message)
            if not message.get("more_body"):
                break

    async def receive():
        return next(messages)

    async def send(message):
        pass

    middleware = UploadBodyLimitMiddleware(app, max_body_bytes=1000, audio_max_body_bytes=10)
    scope = {
        "type": "http",
        "method": "POST",
        "path": f"/api/v1/projects/{uuid4()}/audio",
        "headers": [],
    }
    with pytest.raises(UploadBodyExceeded):
        asyncio.run(middleware(scope, receive, send))
    assert len(seen) == 1


@pytest.mark.parametrize("failure", ["flush", "commit", "storage", "project_deleted"])
def test_audio_publication_failure_compensates(
    domain_client, project, audio_probe, settings, domain_sessions, monkeypatch, failure
):
    if failure in ("flush", "commit"):

        def fail(*_args, **_kwargs):
            raise SQLAlchemyError("secret private path")

        monkeypatch.setattr(Session, failure, fail)
    elif failure == "storage":

        def fail(*_args):
            raise OSError("private path")

        monkeypatch.setattr(LocalStorage, "promote_audio", fail)
    else:
        original = ProjectRepository.get_active
        monkeypatch.setattr(
            ProjectRepository,
            "get_active",
            lambda repo, identifier, for_update=False: (
                None if for_update else original(repo, identifier)
            ),
        )
    response = upload(domain_client, project)
    assert response.status_code == (404 if failure == "project_deleted" else 503)
    assert "private" not in response.text and "secret" not in response.text
    monkeypatch.undo()
    assert_clean(settings, domain_sessions)


def test_audio_lost_commit_ack_preserves_committed_original(
    domain_client, project, audio_probe, settings, domain_sessions, monkeypatch
):
    original = Session.commit

    def commit_disconnect(session):
        original(session)
        raise SQLAlchemyError("lost acknowledgement")

    monkeypatch.setattr(Session, "commit", commit_disconnect)
    assert upload(domain_client, project).status_code == 503
    with domain_sessions() as session:
        assert session.scalar(select(func.count()).select_from(AudioAsset)) == 1
    assert len(list(settings.storage_root.rglob("source.mp3"))) == 1


def test_audio_open_rejects_symlink_and_wrong_key(settings):
    storage = LocalStorage(settings)
    project, identifier = uuid4(), uuid4()
    staged = storage.stage_audio(io.BytesIO(MP3_BYTES))
    key = storage.promote_audio(staged, project, identifier)
    with pytest.raises(AppError):
        storage.open_audio("../private.mp3", project, identifier, len(MP3_BYTES))
    path = settings.storage_root / key
    backup = path.with_name("backup.mp3")
    path.rename(backup)
    path.symlink_to(backup)
    with pytest.raises(AppError):
        storage.open_audio(key, project, identifier, len(MP3_BYTES))


def test_audio_pinned_descriptor_survives_replaced_path(settings):
    storage = LocalStorage(settings)
    project, identifier = uuid4(), uuid4()
    key = storage.promote_audio(storage.stage_audio(io.BytesIO(MP3_BYTES)), project, identifier)
    opened = storage.open_audio(key, project, identifier, len(MP3_BYTES))
    path = settings.storage_root / key
    path.rename(path.with_name("preserved.mp3"))
    path.write_bytes(b"changed")
    try:
        assert opened.file.read() == MP3_BYTES
    finally:
        opened.file.close()


@pytest.mark.parametrize(
    "did_commit,verification_available",
    [(True, True), (False, True), (True, False), (False, False)],
    ids=["committed", "rolled-back", "unknown-committed", "unknown-rolled-back"],
)
def test_audio_commit_outcome_verification_preserves_ambiguous_originals(
    domain_client,
    project,
    audio_probe,
    settings,
    domain_sessions,
    monkeypatch,
    did_commit,
    verification_available,
):
    """Controlled SQLite/SQLAlchemy failures exercise R02 compensation policy."""
    real_commit, real_scalar = Session.commit, Session.scalar
    commit_attempted = False
    verification_queries = []

    def lost_acknowledgement(session):
        nonlocal commit_attempted
        if did_commit:
            real_commit(session)
        commit_attempted = True
        raise SQLAlchemyError("lost commit acknowledgement")

    def verify_after_commit(session, statement, *args, **kwargs):
        if commit_attempted:
            verification_queries.append(statement)
            if not verification_available:
                raise SQLAlchemyError("verification unavailable")
        return real_scalar(session, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "commit", lost_acknowledgement)
    monkeypatch.setattr(Session, "scalar", verify_after_commit)
    response = upload(domain_client, project)
    assert response.status_code == 503
    assert response.json()["code"] == "database_unavailable"
    assert set(response.json()) == {"code", "message", "request_id"}
    assert verification_queries
    # The verifier waits on the same project before resolving the asset row.
    assert verification_queries[0]._for_update_arg is not None
    assert len(verification_queries) == (2 if verification_available else 1)
    assert list((settings.storage_root / "temp").iterdir()) == []

    originals = list(settings.storage_root.rglob("source.mp3"))
    preserve_original = did_commit or not verification_available
    assert len(originals) == int(preserve_original)
    if preserve_original:
        assert originals[0].stat().st_mode & 0o777 == 0o400
        assert (
            hashlib.sha256(originals[0].read_bytes()).hexdigest()
            == hashlib.sha256(MP3_BYTES).hexdigest()
        )
    # Restore verification before independently checking the actual DB outcome.
    monkeypatch.undo()
    with domain_sessions() as session:
        rows = list(session.scalars(select(AudioAsset)))
    assert len(rows) == int(did_commit)
    if did_commit:
        assert rows[0].checksum_sha256 == hashlib.sha256(MP3_BYTES).hexdigest()
        assert rows[0].byte_size == len(MP3_BYTES)
        assert settings.storage_root / rows[0].storage_key == originals[0]


@pytest.mark.parametrize("disconnect_after_rollover", [False, True])
def test_audio_parser_closes_spools_on_disconnect(settings, disconnect_after_rollover):
    """Exercise both in-memory and storage-backed MP3 spools with audio's limit."""
    payload = MP3_BYTES if not disconnect_after_rollover else MP3_BYTES * 130
    body = multipart_body(data=payload, filename="synthetic.mp3", close=False)
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
    parser = SingleFileParser(
        Request(scope, receive), settings, max_file_bytes=settings.audio_upload_max_bytes
    )
    with pytest.raises(ClientDisconnect):
        asyncio.run(parser.parse())
    assert parser.max_file_bytes == settings.audio_upload_max_bytes
    assert parser.file_bytes == len(payload)
    assert parser._files_to_close_on_error
    assert all(spool.closed for spool in parser._files_to_close_on_error)
    assert all(
        spool._rolled is disconnect_after_rollover for spool in parser._files_to_close_on_error
    )
    assert list((settings.storage_root / "temp").iterdir()) == []
