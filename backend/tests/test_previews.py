"""Request/authorization/publication units; MP4 composition is tested separately."""

import hashlib
import io
import stat
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.models.media_asset import MediaAsset
from app.models.preview_job import PreviewJob
from app.models.project import utc_now
from app.storage.local import LocalStorage, StagedFile

ARTIFACT = bytes(range(256)) * 513


@pytest.fixture
def preview_context(domain_client, domain_sessions, settings):
    project = domain_client.post("/api/v1/projects", json={"name": "Preview unit"}).json()
    pid, mid = UUID(project["id"]), uuid4()
    with domain_sessions() as session, session.begin():
        session.add(
            MediaAsset(
                id=mid,
                project_id=pid,
                original_filename="source.mp4",
                storage_key=f"originals/{pid}/{mid}/source.mp4",
                checksum_sha256="a" * 64,
                byte_size=42,
                mime_detected="video/mp4",
                duration_ms=12000,
                width=640,
                height=360,
                fps_num=24,
                fps_den=1,
                video_codec="h264",
                has_audio=False,
                probe_metadata={},
            )
        )
    plan = {
        "schema_version": 1,
        "source_media_id": str(mid),
        "trim": {"start_ms": 2000, "end_ms": 11000},
        "canvas": {"preset": "9:16", "fit": "cover", "focal_x": 0.5, "focal_y": 0.5},
    }
    response = domain_client.put(
        f"/api/v1/projects/{pid}/edit-spec", json={**plan, "expected_revision": 0}
    )
    assert response.status_code == 201
    revision = response.json()
    payload = {
        "edit_spec_revision_id": revision["id"],
        "preview_start_ms": 3000,
        "duration_ms": 8000,
    }
    base = f"/api/v1/projects/{pid}/previews"
    return {"pid": pid, "base": base, "payload": payload, "revision": revision, "plan": plan}


def enqueue(client, context, **changes):
    return client.post(context["base"], json={**context["payload"], **changes})


def complete(client, context, sessions, settings):
    result = enqueue(client, context)
    assert result.status_code == 202
    job_id = UUID(result.json()["id"])
    path = LocalStorage(settings).prepare_temp() / f"{uuid4()}.mp4"
    path.write_bytes(ARTIFACT)
    checksum = hashlib.sha256(ARTIFACT).hexdigest()
    key = LocalStorage(settings).promote_preview(
        StagedFile(path, len(ARTIFACT), checksum), context["pid"], job_id
    )
    with sessions() as session, session.begin():
        job = session.get(PreviewJob, job_id)
        job.status = job.stage = "completed"
        job.artifact_storage_key, job.artifact_sha256 = key, checksum
        job.artifact_byte_size, job.finished_at = len(ARTIFACT), utc_now()
    return result.json(), settings.storage_root / key


def assert_error(response, status, code=None):
    assert response.status_code == status, response.text
    data = response.json()
    assert set(data) == {"code", "message", "request_id"}
    assert data["request_id"] == response.headers["x-request-id"]
    if code:
        assert data["code"] == code
    assert "Traceback" not in response.text and "/private" not in response.text


def test_enqueue_is_pending_and_idempotent_without_encoding(
    domain_client, preview_context, monkeypatch
):
    import subprocess

    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **k: pytest.fail("API must not spawn encoding")
    )
    first, second = enqueue(domain_client, preview_context), enqueue(domain_client, preview_context)
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    body = first.json()
    assert body["status"] == body["stage"] == "queued" and body["attempts"] == 0
    assert body["revision_id"] == preview_context["revision"]["id"]
    assert body["artifact_available"] is False and body["file_url"] is None
    assert body["checksum_sha256"] is body["byte_size"] is None
    assert body["status_url"].startswith("/api/")
    assert "storage_key" not in first.text and "claim_token" not in first.text
    assert domain_client.get(body["status_url"]).json() == body


@pytest.mark.parametrize(
    "changes",
    [
        {"duration_ms": 0},
        {"duration_ms": 8001},
        {"duration_ms": True},
        {"duration_ms": 1.0},
        {"duration_ms": "1000"},
        {"preview_start_ms": -1},
        {"preview_start_ms": False},
        {"preview_start_ms": 2000.0},
        {"edit_spec_revision_id": "bad"},
        {"unknown": "path"},
        {"spec_json": {}},
        {"duration_ms": None},
        {"preview_start_ms": 1999},
        {"preview_start_ms": 11000},
    ],
)
def test_invalid_windows_and_types(domain_client, preview_context, changes):
    assert_error(enqueue(domain_client, preview_context, **changes), 422)


def test_duration_truncated_and_equivalent_window_reused(domain_client, preview_context):
    first = enqueue(domain_client, preview_context, preview_start_ms=10999, duration_ms=8000)
    second = enqueue(domain_client, preview_context, preview_start_ms=10999, duration_ms=1)
    assert first.status_code == second.status_code == 202
    assert first.json()["duration_ms"] == 1 and first.json()["id"] == second.json()["id"]


def test_active_limit_does_not_block_equivalent_pending(domain_client, preview_context):
    assert enqueue(domain_client, preview_context).status_code == 202
    assert enqueue(domain_client, preview_context, preview_start_ms=4000).status_code == 202
    assert enqueue(domain_client, preview_context).status_code == 202
    assert_error(
        enqueue(domain_client, preview_context, preview_start_ms=5000), 409, "preview_active_limit"
    )


def test_unknown_or_foreign_revision_is_not_found(domain_client, preview_context):
    assert_error(enqueue(domain_client, preview_context, edit_spec_revision_id=str(uuid4())), 404)
    other = domain_client.post("/api/v1/projects", json={"name": "Other"}).json()["id"]
    assert_error(
        domain_client.post(f"/api/v1/projects/{other}/previews", json=preview_context["payload"]),
        404,
    )


def test_old_revision_is_retained_and_jobs_are_distinct(domain_client, preview_context):
    old = enqueue(domain_client, preview_context).json()
    plan = preview_context["plan"] | {
        "canvas": {"preset": "1:1", "fit": "contain", "focal_x": 0.5, "focal_y": 0.5}
    }
    revision = domain_client.put(
        f"/api/v1/projects/{preview_context['pid']}/edit-spec", json=plan | {"expected_revision": 1}
    ).json()
    new = enqueue(domain_client, preview_context, edit_spec_revision_id=revision["id"]).json()
    assert old["revision_number"] == 1 and new["revision_number"] == 2
    assert old["id"] != new["id"]
    assert domain_client.get(old["status_url"]).json()["revision_id"] == old["revision_id"]
    assert enqueue(domain_client, preview_context).json()["id"] == old["id"]


def test_pending_and_failed_have_no_file(domain_client, preview_context, domain_sessions):
    job = enqueue(domain_client, preview_context).json()
    assert_error(domain_client.get(job["status_url"] + "/file"), 409, "preview_not_ready")
    with domain_sessions() as session, session.begin():
        model = session.get(PreviewJob, UUID(job["id"]))
        model.status = model.stage = "failed"
        model.error_code = "raw /private/password"
    data = domain_client.get(job["status_url"]).json()
    assert data["error_code"] == "preview_processing_failed"
    assert_error(domain_client.get(job["status_url"] + "/file"), 409)
    replacement = enqueue(domain_client, preview_context)
    assert replacement.status_code == 202 and replacement.json()["id"] != job["id"]


@pytest.mark.parametrize(
    "range_value,start,end,status",
    [
        (None, 0, len(ARTIFACT), 200),
        ("bytes=0-15", 0, 16, 206),
        ("bytes=1024-2047", 1024, 2048, 206),
        ("bytes=65536-", 65536, len(ARTIFACT), 206),
        ("bytes=-13", len(ARTIFACT) - 13, len(ARTIFACT), 206),
        ("bytes=7-7", 7, 8, 206),
    ],
)
def test_completed_preview_private_ranges(
    domain_client, preview_context, domain_sessions, settings, range_value, start, end, status
):
    job, path = complete(domain_client, preview_context, domain_sessions, settings)
    before = path.stat()
    headers = {"Range": range_value} if range_value else {}
    file_url = job["status_url"] + "/file"
    response = domain_client.get(file_url, headers=headers)
    head = domain_client.head(file_url, headers=headers)
    assert response.status_code == head.status_code == status
    assert response.content == ARTIFACT[start:end] and head.content == b""
    assert int(response.headers["content-length"]) == end - start
    for name, value in {
        "accept-ranges": "bytes",
        "cache-control": "private, no-store",
        "x-content-type-options": "nosniff",
        "content-type": "video/mp4",
    }.items():
        assert response.headers[name] == head.headers[name] == value
    if status == 206:
        assert response.headers["content-range"] == f"bytes {start}-{end - 1}/{len(ARTIFACT)}"
    assert path.read_bytes() == ARTIFACT
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    assert stat.S_IMODE(path.stat().st_mode) == 0o400
    reused = enqueue(domain_client, preview_context)
    assert reused.status_code == 200 and reused.json()["id"] == job["id"]
    assert reused.json()["artifact_available"] and reused.json()["file_url"] == file_url


@pytest.mark.parametrize(
    "range_value", ["bytes=0-1,5-6", "bytes=-0", "items=0-1", "bytes=999999-", "bytes=a-b"]
)
def test_preview_invalid_ranges_416(
    domain_client, preview_context, domain_sessions, settings, range_value
):
    job, _path = complete(domain_client, preview_context, domain_sessions, settings)
    response = domain_client.get(job["status_url"] + "/file", headers={"Range": range_value})
    assert_error(response, 416, "range_not_satisfiable")
    assert response.headers["content-range"] == f"bytes */{len(ARTIFACT)}"
    assert (
        domain_client.head(job["status_url"] + "/file", headers={"Range": range_value}).content
        == b""
    )


@pytest.mark.parametrize(
    "damage", ["missing", "checksum", "mode", "symlink", "directory", "traversal", "oversize"]
)
def test_unavailable_artifact_never_reused_and_preserved(
    domain_client, preview_context, domain_sessions, settings, tmp_path, damage
):
    job, path = complete(domain_client, preview_context, domain_sessions, settings)
    if damage == "missing":
        path.unlink()
    elif damage == "checksum":
        path.chmod(0o600)
        path.write_bytes(b"x" * len(ARTIFACT))
        path.chmod(0o400)
    elif damage == "mode":
        path.chmod(0o600)
    elif damage == "symlink":
        path.unlink()
        secret = tmp_path / "private-secret"
        secret.write_bytes(ARTIFACT)
        path.symlink_to(secret)
    elif damage == "directory":
        path.unlink()
        path.mkdir()
    else:
        with domain_sessions() as session, session.begin():
            model = session.get(PreviewJob, UUID(job["id"]))
            if damage == "traversal":
                model.artifact_storage_key = "../private-secret"
            else:
                model.artifact_byte_size = settings.preview_max_bytes + 1
    assert_error(domain_client.get(job["status_url"]), 503, "preview_artifact_unavailable")
    assert_error(domain_client.get(job["status_url"] + "/file"), 503)
    result = enqueue(domain_client, preview_context)
    assert result.status_code == 202 and result.json()["id"] != job["id"]
    with domain_sessions() as session:
        assert session.get(PreviewJob, UUID(job["id"])).status == "failed"
    if damage != "missing":
        assert path.exists() or path.is_symlink()


def test_cross_project_deleted_and_unknown_never_open_storage(
    domain_client, preview_context, monkeypatch
):
    job = enqueue(domain_client, preview_context).json()
    other = domain_client.post("/api/v1/projects", json={"name": "Other"}).json()["id"]
    monkeypatch.setattr(
        LocalStorage, "open_preview", lambda *a: pytest.fail("unauthorized storage read")
    )
    for url in [
        f"/api/v1/projects/{other}/previews/{job['id']}",
        preview_context["base"] + "/" + str(uuid4()),
    ]:
        assert_error(domain_client.get(url), 404)
        assert_error(domain_client.get(url + "/file"), 404)
    assert domain_client.delete(f"/api/v1/projects/{preview_context['pid']}").status_code == 204
    assert_error(domain_client.get(job["status_url"]), 404)
    assert_error(enqueue(domain_client, preview_context), 404)
    assert_error(domain_client.get(job["status_url"] + "/file"), 404)


def test_database_failure_sanitized(domain_client, preview_context, monkeypatch):
    def failure(*args, **kwargs):
        raise SQLAlchemyError("postgresql://private:secret@db /private/file")

    monkeypatch.setattr(
        "app.repositories.preview_jobs.PreviewJobRepository.get_equivalent", failure
    )
    assert_error(enqueue(domain_client, preview_context), 503, "database_unavailable")


def test_publication_is_exclusive_and_staging_cleanup(settings):
    storage = LocalStorage(settings)
    pid, jid = uuid4(), uuid4()
    staged = storage.stage(io.BytesIO(ARTIFACT))
    key = storage.promote_preview(staged, pid, jid)
    assert not staged.path.exists()
    before = (settings.storage_root / key).read_bytes()
    second = storage.stage(io.BytesIO(b"replacement"))
    from app.core.errors import AppError

    with pytest.raises(AppError) as error:
        storage.promote_preview(second, pid, jid)
    assert error.value.code == "preview_publication_interrupted"
    assert second.path.exists()
    assert (settings.storage_root / key).read_bytes() == before


def test_stream_transaction_released_and_constructor_failure_closes(
    domain_client, preview_context, domain_sessions, settings, monkeypatch
):
    from app.api.media_stream import PrivateMediaResponse

    job, _path = complete(domain_client, preview_context, domain_sessions, settings)
    real = PrivateMediaResponse.__call__
    rolled_back = []
    from app.api.v1 import previews

    rollback = previews.safe_rollback

    def tracked(session):
        rollback(session)
        rolled_back.append(session)

    async def inspect(response, scope, receive, send):
        assert rolled_back and not rolled_back[-1].in_transaction()
        await real(response, scope, receive, send)

    monkeypatch.setattr(previews, "safe_rollback", tracked)
    monkeypatch.setattr(PrivateMediaResponse, "__call__", inspect)
    assert domain_client.get(job["status_url"] + "/file").status_code == 200
    opened = []
    original = LocalStorage.open_preview

    def remember(*args):
        result = original(*args)
        opened.append(result)
        return result

    monkeypatch.setattr(LocalStorage, "open_preview", remember)

    def fail(*args):
        raise RuntimeError("/private/secret")

    monkeypatch.setattr(previews, "PrivateMediaResponse", fail)
    assert_error(domain_client.get(job["status_url"] + "/file"), 500)
    assert opened[0].file.closed
