"""API/storage/state units use synthetic bytes, not a real FFmpeg renderer.

PostgreSQL concurrency and real full exports are separate operational gates.
"""

import hashlib
import io
import stat
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.media_asset import MediaAsset
from app.models.project import utc_now
from app.models.render_job import RenderJob
from app.storage.local import LocalStorage, StagedFile

ARTIFACT = bytes(range(256)) * 513


@pytest.fixture
def render_context(domain_client, domain_sessions, settings, monkeypatch):
    settings.storage_min_free_bytes = 0
    settings.render_max_output_bytes = 1024 * 1024
    monkeypatch.setattr(
        "app.repositories.render_jobs.database_now", lambda: func.current_timestamp()
    )
    return seed_render_project(domain_client, domain_sessions, settings)


def seed_render_project(domain_client, domain_sessions, settings):
    """Each isolated API unit gets its own project, original and saved revision."""
    pid = UUID(domain_client.post("/api/v1/projects", json={"name": "Render unit"}).json()["id"])
    mid = uuid4()
    storage = LocalStorage(settings)
    staged = storage.stage(io.BytesIO(b"synthetic original bytes; not an encoded video"))
    key = storage.promote(staged, pid, mid)
    with domain_sessions() as session, session.begin():
        session.add(
            MediaAsset(
                id=mid,
                project_id=pid,
                original_filename="source.mp4",
                storage_key=key,
                checksum_sha256=staged.checksum_sha256,
                byte_size=staged.byte_size,
                mime_detected="video/mp4",
                duration_ms=20000,
                width=640,
                height=360,
                fps_num=24,
                fps_den=1,
                video_codec="h264",
                has_audio=False,
                probe_metadata={"video_stream_index": 0},
            )
        )
    plan = {
        "schema_version": 1,
        "source_media_id": str(mid),
        "trim": {"start_ms": 2000, "end_ms": 15000},
        "canvas": {"preset": "9:16", "fit": "cover", "focal_x": 0.5, "focal_y": 0.5},
    }
    response = domain_client.put(
        f"/api/v1/projects/{pid}/edit-spec", json={**plan, "expected_revision": 0}
    )
    assert response.status_code == 201
    revision = response.json()
    return {
        "pid": pid,
        "mid": mid,
        "base": f"/api/v1/projects/{pid}/renders",
        "revision": revision,
        "plan": plan,
        "payload": {"edit_spec_revision_id": revision["id"], "profile": "standard"},
        "source_path": settings.storage_root / key,
    }


def enqueue(client, context, **changes):
    return client.post(context["base"], json={**context["payload"], **changes})


def assert_error(response, status, code=None):
    assert response.status_code == status, response.text
    data = response.json()
    assert set(data) == {"code", "message", "request_id"}
    assert data["request_id"] == response.headers["x-request-id"]
    if code:
        assert data["code"] == code
    assert "Traceback" not in response.text and "/private" not in response.text


def complete(client, context, sessions, settings, **changes):
    response = enqueue(client, context, **changes)
    assert response.status_code == 202, response.text
    job = response.json()
    path = LocalStorage(settings).prepare_temp() / f"{uuid4()}.mp4"
    path.write_bytes(ARTIFACT)
    checksum = hashlib.sha256(ARTIFACT).hexdigest()
    key = LocalStorage(settings).promote_render(
        StagedFile(path, len(ARTIFACT), checksum), context["pid"], UUID(job["id"])
    )
    with sessions() as session, session.begin():
        model = session.get(RenderJob, UUID(job["id"]))
        model.status = model.stage = "completed"
        model.attempts = 1
        model.artifact_storage_key, model.artifact_sha256 = key, checksum
        model.artifact_byte_size, model.finished_at = len(ARTIFACT), utc_now()
        model.artifact_duration_ms = 13000
        model.artifact_width, model.artifact_height = 1080, 1920
        model.artifact_video_codec = "h264"
        model.artifact_fps_num, model.artifact_fps_den = 30, 1
    return job, settings.storage_root / key


def test_enqueue_reuses_equivalent_without_spawning(domain_client, render_context, monkeypatch):
    import subprocess

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("no encoding in HTTP"))
    first, second = enqueue(domain_client, render_context), enqueue(domain_client, render_context)
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    body = first.json()
    assert body["status"] == body["stage"] == "queued" and body["attempts"] == 0
    assert body["revision_id"] == render_context["revision"]["id"]
    assert body["artifact_available"] is False and body["file_url"] is None
    assert body["progress_percent"] is body["progress_encoded_ms"] is None
    assert body["checksum_sha256"] is body["byte_size"] is None
    for private in [
        "storage_key",
        "claim_token",
        "lease_expires_at",
        "idempotency_key",
        "request_fingerprint",
    ]:
        assert private not in body
    assert domain_client.get(body["status_url"]).json() == body


def test_global_queue_full_blocks_another_project_but_keeps_active_replays(
    domain_client, render_context, domain_sessions, settings
):
    settings.render_max_active_total = 1
    other = seed_render_project(domain_client, domain_sessions, settings)
    key = str(uuid4())
    first = enqueue(domain_client, render_context, idempotency_key=key)
    assert first.status_code == 202
    assert_error(enqueue(domain_client, other), 409, "render_queue_full")
    for payload in ({}, {"idempotency_key": key}):
        replay = enqueue(domain_client, render_context, **payload)
        assert replay.status_code == 202 and replay.json()["id"] == first.json()["id"]
    with domain_sessions() as session:
        assert session.scalar(select(func.count()).select_from(RenderJob)) == 1


@pytest.mark.parametrize("terminal", ["canceled", "failed", "completed"])
def test_global_queue_slot_released_by_terminal_but_terminal_replay_still_works_at_cap(
    domain_client, render_context, domain_sessions, settings, terminal
):
    settings.render_max_active_total = 1
    other = seed_render_project(domain_client, domain_sessions, settings)
    key = str(uuid4())
    first = enqueue(domain_client, render_context, idempotency_key=key).json()
    assert_error(enqueue(domain_client, other), 409, "render_queue_full")
    if terminal == "completed":
        complete(domain_client, render_context, domain_sessions, settings)
    elif terminal == "canceled":
        assert domain_client.post(first["status_url"] + "/cancel").status_code == 200
    else:
        with domain_sessions() as session, session.begin():
            job = session.get(RenderJob, UUID(first["id"]))
            job.status = job.stage = "failed"
            job.finished_at = utc_now()
    assert enqueue(domain_client, other).status_code == 202
    replay = enqueue(domain_client, render_context, idempotency_key=key)
    assert replay.status_code == 200
    assert replay.json()["id"] == first["id"] and replay.json()["status"] == terminal
    if terminal == "completed":
        equivalent = enqueue(domain_client, render_context)
        assert equivalent.status_code == 200 and equivalent.json()["id"] == first["id"]


def test_deleted_queued_project_still_counts_until_existing_recovery_releases_slot(
    domain_client, render_context, domain_sessions, settings
):
    from app.repositories.render_jobs import RenderJobRepository

    settings.render_max_active_total = 1
    other = seed_render_project(domain_client, domain_sessions, settings)
    original = render_context["source_path"].read_bytes()
    first = enqueue(domain_client, render_context).json()
    assert domain_client.delete(f"/api/v1/projects/{render_context['pid']}").status_code == 204
    assert_error(enqueue(domain_client, other), 409, "render_queue_full")
    with domain_sessions() as session, session.begin():
        repository = RenderJobRepository(session)
        assert repository.count_active_total() == 1
        assert repository.recover_expired(project_id=render_context["pid"]) == 1
        assert repository.count_active_total() == 0
    assert enqueue(domain_client, other).status_code == 202
    with domain_sessions() as session:
        retained = session.get(RenderJob, UUID(first["id"]))
        assert retained.status == "failed" and retained.error_code == "render_project_unavailable"
    assert render_context["source_path"].read_bytes() == original


@pytest.mark.parametrize(
    "changes",
    [
        {"profile": "ultra"},
        {"profile": None},
        {"profile": 1},
        {"edit_spec_revision_id": "bad"},
        {"edit_spec_revision_id": None},
        {"idempotency_key": "bad"},
        {"idempotency_key": True},
        {"idempotency_key": 1},
        {"idempotency_key": []},
        {"unknown": "path"},
        {"spec_json": {}},
        {"source_media_id": str(uuid4())},
    ],
)
def test_invalid_payloads(domain_client, render_context, changes):
    assert_error(enqueue(domain_client, render_context, **changes), 422)


def test_explicit_key_is_canonical_permanent_and_conflicts(domain_client, render_context):
    key = str(uuid4())
    first = enqueue(domain_client, render_context, idempotency_key=key.upper())
    second = enqueue(domain_client, render_context, idempotency_key=key)
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert_error(
        enqueue(domain_client, render_context, idempotency_key=key, profile="high"),
        409,
        "idempotency_conflict",
    )
    assert_error(
        enqueue(domain_client, render_context, idempotency_key=str(uuid4())),
        409,
        "render_active_limit",
    )
    # A keyless request may reuse equivalent work originally requested with a key.
    assert enqueue(domain_client, render_context).json()["id"] == first.json()["id"]


@pytest.mark.parametrize("terminal", ["failed", "canceled"])
def test_terminal_key_replay_never_reopens_and_new_key_is_explicit(
    domain_client, render_context, domain_sessions, terminal
):
    key = str(uuid4())
    first = enqueue(domain_client, render_context, idempotency_key=key).json()
    with domain_sessions() as session, session.begin():
        model = session.get(RenderJob, UUID(first["id"]))
        model.status = model.stage = terminal
        model.finished_at = utc_now()
        model.error_code = "render_encoding_failed" if terminal == "failed" else None
        if terminal == "canceled":
            model.cancel_requested_at = utc_now()
    replay = enqueue(domain_client, render_context, idempotency_key=key)
    assert replay.status_code == 200
    assert replay.json()["id"] == first["id"] and replay.json()["status"] == terminal
    next_job = enqueue(domain_client, render_context, idempotency_key=str(uuid4()))
    assert next_job.status_code == 202 and next_job.json()["id"] != first["id"]


def test_unknown_foreign_and_deleted_projects_cannot_enqueue(
    domain_client, render_context, monkeypatch
):
    assert_error(enqueue(domain_client, render_context, edit_spec_revision_id=str(uuid4())), 404)
    other = domain_client.post("/api/v1/projects", json={"name": "Other"}).json()["id"]
    assert_error(
        domain_client.post(f"/api/v1/projects/{other}/renders", json=render_context["payload"]), 404
    )
    body = enqueue(domain_client, render_context).json()
    monkeypatch.setattr(LocalStorage, "open_render", lambda *a: pytest.fail("unauthorized storage"))
    assert_error(domain_client.get(f"/api/v1/projects/{other}/renders/{body['id']}"), 404)
    assert_error(domain_client.get(f"/api/v1/projects/{other}/renders/{body['id']}/file"), 404)
    assert_error(domain_client.post(f"/api/v1/projects/{other}/renders/{body['id']}/cancel"), 404)
    assert domain_client.delete(f"/api/v1/projects/{render_context['pid']}").status_code == 204
    assert_error(domain_client.get(body["status_url"]), 404)
    assert_error(domain_client.get(body["status_url"] + "/file"), 404)
    assert_error(domain_client.get(render_context["base"]), 404)
    assert_error(domain_client.post(body["status_url"] + "/cancel"), 404)
    assert_error(enqueue(domain_client, render_context), 404)


def test_old_saved_revision_is_used_after_new_save(domain_client, render_context, domain_sessions):
    old = enqueue(domain_client, render_context).json()
    domain_client.post(old["status_url"] + "/cancel")
    changed = render_context["plan"] | {
        "canvas": {"preset": "1:1", "fit": "contain", "focal_x": 0.5, "focal_y": 0.5}
    }
    revision = domain_client.put(
        f"/api/v1/projects/{render_context['pid']}/edit-spec",
        json=changed | {"expected_revision": 1},
    ).json()
    new = enqueue(domain_client, render_context, edit_spec_revision_id=revision["id"]).json()
    assert old["revision_number"] == 1 and new["revision_number"] == 2
    assert domain_client.get(old["status_url"]).json()["preset"] == "9:16"
    assert new["preset"] == "1:1"
    with domain_sessions() as session:
        assert session.get(RenderJob, UUID(old["id"])).edit_spec_revision_id == UUID(
            old["revision_id"]
        )


def test_queued_cancel_is_immediate_and_idempotent_without_signals(
    domain_client, render_context, monkeypatch
):
    import os

    monkeypatch.setattr(os, "killpg", lambda *a: pytest.fail("HTTP cannot signal processes"))
    job = enqueue(domain_client, render_context).json()
    canceled = domain_client.post(job["status_url"] + "/cancel")
    assert canceled.status_code == 200
    body = canceled.json()
    assert body["status"] == body["stage"] == "canceled"
    assert body["cancel_requested_at"] and body["finished_at"] and not body["artifact_available"]
    assert domain_client.post(job["status_url"] + "/cancel").json() == body
    assert_error(domain_client.get(job["status_url"] + "/file"), 409, "render_not_ready")


def test_running_cancel_only_records_intent(domain_client, render_context, domain_sessions):
    job = enqueue(domain_client, render_context).json()
    with domain_sessions() as session, session.begin():
        model = session.get(RenderJob, UUID(job["id"]))
        model.status, model.stage, model.attempts = "running", "encoding", 1
        model.claim_token = uuid4()
        model.heartbeat_at = utc_now()
        model.lease_expires_at = utc_now() + timedelta(seconds=30)
        model.progress_encoded_ms = 7000
    response = domain_client.post(job["status_url"] + "/cancel")
    assert response.status_code == 202
    assert response.json()["status"] == "running" and response.json()["cancel_requested_at"]
    assert response.json()["finished_at"] is None
    assert domain_client.post(job["status_url"] + "/cancel").status_code == 202


@pytest.mark.parametrize(
    "stage,encoded,percent",
    [
        ("encoding", 7000, 53),
        ("encoding", 13000, 99),
        ("encoding", 0, 0),
        ("validating", 13000, None),
        ("publishing", 13000, None),
    ],
)
def test_progress_is_measured_only_during_encoding(
    domain_client, render_context, domain_sessions, stage, encoded, percent
):
    job = enqueue(domain_client, render_context).json()
    with domain_sessions() as session, session.begin():
        model = session.get(RenderJob, UUID(job["id"]))
        model.status, model.stage, model.attempts = "running", stage, 1
        model.claim_token = uuid4()
        model.heartbeat_at = utc_now()
        model.lease_expires_at = utc_now() + timedelta(seconds=30)
        model.progress_encoded_ms = encoded
    detail = domain_client.get(job["status_url"]).json()
    assert detail["progress_encoded_ms"] == encoded
    assert detail["progress_percent"] == percent


@pytest.mark.parametrize("terminal", ["failed", "completed"])
def test_terminal_not_cancelable(
    domain_client, render_context, domain_sessions, settings, terminal
):
    if terminal == "completed":
        job, _ = complete(domain_client, render_context, domain_sessions, settings)
    else:
        job = enqueue(domain_client, render_context).json()
        with domain_sessions() as session, session.begin():
            model = session.get(RenderJob, UUID(job["id"]))
            model.status = model.stage = "failed"
            model.finished_at = utc_now()
    assert_error(domain_client.post(job["status_url"] + "/cancel"), 409, "render_not_cancelable")


def test_history_pagination_filters_and_deterministic_ties(
    domain_client, render_context, domain_sessions
):
    identifiers = []
    for _ in range(5):
        job = enqueue(domain_client, render_context, idempotency_key=str(uuid4())).json()
        identifiers.append(job["id"])
        assert domain_client.post(job["status_url"] + "/cancel").status_code == 200
    now = datetime(2026, 10, 21, tzinfo=UTC)
    with domain_sessions() as session, session.begin():
        for model in session.scalars(
            select(RenderJob).where(RenderJob.project_id == render_context["pid"])
        ):
            model.created_at = now
    first = domain_client.get(render_context["base"], params={"limit": 2, "offset": 0}).json()
    second = domain_client.get(render_context["base"], params={"limit": 2, "offset": 2}).json()
    assert first["total"] == second["total"] == 5
    assert [item["id"] for item in first["items"] + second["items"]] == sorted(
        identifiers, reverse=True
    )[:4]
    assert first["limit"] == 2 and second["offset"] == 2
    assert (
        domain_client.get(render_context["base"], params={"status": "canceled"}).json()["total"]
        == 5
    )
    assert (
        domain_client.get(render_context["base"], params={"status": "completed"}).json()["total"]
        == 0
    )


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"status": "invalid"}]
)
def test_invalid_history_query(domain_client, render_context, params):
    assert_error(domain_client.get(render_context["base"], params=params), 422)


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
def test_final_private_download_ranges_and_original_integrity(
    domain_client, render_context, domain_sessions, settings, range_value, start, end, status
):
    original = render_context["source_path"].read_bytes()
    job, path = complete(domain_client, render_context, domain_sessions, settings)
    before = path.stat()
    headers = {"Range": range_value} if range_value else {}
    file_url = job["status_url"] + "/file"
    response, head = (
        domain_client.get(file_url, headers=headers),
        domain_client.head(file_url, headers=headers),
    )
    assert response.status_code == head.status_code == status
    assert response.content == ARTIFACT[start:end] and head.content == b""
    assert int(response.headers["content-length"]) == end - start
    for key, value in {
        "accept-ranges": "bytes",
        "cache-control": "private, no-store",
        "content-type": "video/mp4",
        "x-content-type-options": "nosniff",
    }.items():
        assert response.headers[key] == head.headers[key] == value
    assert "attachment" in response.headers["content-disposition"]
    assert "edivo-r1-9x16-standard.mp4" in response.headers["content-disposition"]
    if status == 206:
        assert response.headers["content-range"] == f"bytes {start}-{end - 1}/{len(ARTIFACT)}"
    assert path.read_bytes() == ARTIFACT and path.stat().st_mtime_ns == before.st_mtime_ns
    assert stat.S_IMODE(path.stat().st_mode) == 0o400
    assert render_context["source_path"].read_bytes() == original
    assert stat.S_IMODE(render_context["source_path"].stat().st_mode) == 0o400
    ready = domain_client.get(job["status_url"]).json()
    assert ready["artifact_available"] and ready["progress_percent"] == 100
    assert ready["duration_ms"] == 13000 and ready["width"] == 1080 and ready["height"] == 1920
    assert enqueue(domain_client, render_context).status_code == 200


@pytest.mark.parametrize(
    "range_value", ["bytes=0-1,5-6", "bytes=-0", "items=0-1", "bytes=999999-", "bytes=a-b"]
)
def test_final_invalid_ranges_416(
    domain_client, render_context, domain_sessions, settings, range_value
):
    job, _ = complete(domain_client, render_context, domain_sessions, settings)
    response = domain_client.get(job["status_url"] + "/file", headers={"Range": range_value})
    assert_error(response, 416, "range_not_satisfiable")
    assert response.headers["content-range"] == f"bytes */{len(ARTIFACT)}"
    assert "attachment" in response.headers["content-disposition"]
    assert (
        domain_client.head(job["status_url"] + "/file", headers={"Range": range_value}).content
        == b""
    )


@pytest.mark.parametrize(
    "damage", ["missing", "checksum", "mode", "symlink", "directory", "traversal", "oversize"]
)
def test_bad_artifact_keeps_completed_history_and_requires_explicit_new_identity(
    domain_client, render_context, domain_sessions, settings, tmp_path, damage
):
    key = str(uuid4())
    job, path = complete(
        domain_client, render_context, domain_sessions, settings, idempotency_key=key
    )
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
        external = tmp_path / "private-secret"
        external.write_bytes(ARTIFACT)
        path.symlink_to(external)
    elif damage == "directory":
        path.unlink()
        path.mkdir()
    else:
        with domain_sessions() as session, session.begin():
            model = session.get(RenderJob, UUID(job["id"]))
            if damage == "traversal":
                model.artifact_storage_key = "../private-secret"
            else:
                model.artifact_byte_size = settings.render_max_output_bytes + 1
    detail = domain_client.get(job["status_url"])
    assert detail.status_code == 200 and detail.json()["status"] == "completed"
    assert (
        not detail.json()["artifact_available"]
        and detail.json()["error_code"] == "render_artifact_unavailable"
    )
    assert_error(domain_client.get(job["status_url"] + "/file"), 503, "render_artifact_unavailable")
    assert_error(
        enqueue(domain_client, render_context, idempotency_key=key),
        503,
        "render_artifact_unavailable",
    )
    next_job = enqueue(domain_client, render_context, idempotency_key=str(uuid4()))
    assert next_job.status_code == 202 and next_job.json()["id"] != job["id"]
    with domain_sessions() as session:
        assert session.get(RenderJob, UUID(job["id"])).status == "completed"
    if damage != "missing":
        assert path.exists() or path.is_symlink()


@pytest.mark.parametrize("fault", ["missing", "mode"])
def test_missing_or_inaccessible_source_rejected_before_enqueue(
    domain_client, render_context, fault
):
    if fault == "missing":
        render_context["source_path"].unlink()
    else:
        render_context["source_path"].chmod(0o600)
    assert_error(enqueue(domain_client, render_context), 503, "render_source_unavailable")


def test_engine_version_invalidates_reuse_but_reserves_permanent_key(
    domain_client, render_context, domain_sessions, settings, monkeypatch
):
    key = str(uuid4())
    old, _ = complete(domain_client, render_context, domain_sessions, settings, idempotency_key=key)
    monkeypatch.setattr("app.services.renders.RENDER_ENGINE_VERSION", "r05-2")
    repeated = enqueue(domain_client, render_context, idempotency_key=key)
    assert_error(repeated, 409, "render_engine_conflict")
    assert domain_client.get(old["status_url"]).json()["status"] == "completed"
    assert domain_client.get(old["status_url"] + "/file").content == ARTIFACT
    new = enqueue(domain_client, render_context)
    assert new.status_code == 202 and new.json()["render_engine_version"] == "r05-2"


def test_commit_failure_sanitized_and_rolled_back(
    domain_client, render_context, domain_sessions, monkeypatch
):
    def fail(*args, **kwargs):
        raise SQLAlchemyError("postgresql://private:secret@db /private/file")

    monkeypatch.setattr(Session, "commit", fail)
    assert_error(enqueue(domain_client, render_context), 503, "database_unavailable")
    with domain_sessions() as session:
        assert session.scalar(select(func.count()).select_from(RenderJob)) == 0


def test_stream_closes_fd_on_response_constructor_failure(
    domain_client, render_context, domain_sessions, settings, monkeypatch
):
    job, _ = complete(domain_client, render_context, domain_sessions, settings)
    opened = []
    original = LocalStorage.open_render

    def remember(*args):
        result = original(*args)
        opened.append(result)
        return result

    monkeypatch.setattr(LocalStorage, "open_render", remember)

    def fail(*args, **kwargs):
        raise RuntimeError("/private/secret")

    monkeypatch.setattr("app.api.v1.renders.PrivateMediaResponse", fail)
    assert_error(domain_client.get(job["status_url"] + "/file"), 500)
    assert opened and opened[0].file.closed
