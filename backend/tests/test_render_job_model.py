"""Relational export constraints; PostgreSQL locks are verified separately."""

import runpy
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import CheckConstraint
from sqlalchemy.exc import IntegrityError
from test_edit_spec import plan, seed_source

from app.models.edit_spec_revision import EditSpecRevision
from app.models.project import utc_now
from app.models.render_job import RenderJob


@pytest.fixture
def job_context(domain_client, domain_sessions):
    pid = UUID(
        domain_client.post("/api/v1/projects", json={"name": "Render constraints"}).json()["id"]
    )
    mid = seed_source(domain_sessions, pid)
    with domain_sessions() as session, session.begin():
        revision = EditSpecRevision(
            project_id=pid,
            source_media_id=mid,
            revision_number=1,
            schema_version=1,
            spec_json={k: v for k, v in plan(mid).items() if k != "expected_revision"},
        )
        session.add(revision)
        session.flush()
        return pid, revision.id


def make_job(context, **changes):
    values = dict(
        project_id=context[0],
        edit_spec_revision_id=context[1],
        profile="standard",
        render_engine_version="r05-1",
        request_fingerprint="a" * 64,
    )
    values.update(changes)
    return RenderJob(**values)


def completed_values():
    return dict(
        status="completed",
        stage="completed",
        attempts=1,
        finished_at=utc_now(),
        artifact_storage_key=f"renders/{uuid4()}/final.mp4",
        artifact_sha256="b" * 64,
        artifact_byte_size=200,
        artifact_duration_ms=1000,
        artifact_width=1920,
        artifact_height=1080,
        artifact_video_codec="h264",
        artifact_fps_num=30,
        artifact_fps_den=1,
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"profile": "ultra"},
        {"render_engine_version": ""},
        {"idempotency_key": "bad"},
        {"request_fingerprint": "bad"},
        {"request_fingerprint": "A" * 64},
        {"attempts": -1},
        {"attempts": 3},
        {"max_attempts": 0},
        {"max_attempts": 3},
        {"max_attempts": 1, "attempts": 2},
        {"status": "unknown"},
        {"stage": "encoding"},
        {"status": "running", "stage": "preparing"},
        {"claim_token": uuid4()},
        {"progress_encoded_ms": -1},
        {"status": "completed", "stage": "completed"},
        {"status": "failed", "stage": "failed"},
        {"status": "canceled", "stage": "canceled", "finished_at": utc_now()},
        {"artifact_byte_size": 0},
        {"artifact_sha256": "bad"},
        {"artifact_storage_key": "unexpected"},
        {"artifact_duration_ms": 1},
        {"project_id": uuid4()},
        {"edit_spec_revision_id": uuid4()},
    ],
)
def test_database_rejects_incoherent_state(domain_sessions, job_context, changes):
    with domain_sessions() as session:
        session.add(make_job(job_context, **changes))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


@pytest.mark.parametrize(
    "field",
    [
        "artifact_duration_ms",
        "artifact_width",
        "artifact_height",
        "artifact_video_codec",
        "artifact_fps_num",
        "artifact_fps_den",
        "artifact_sha256",
        "artifact_byte_size",
        "artifact_storage_key",
        "finished_at",
    ],
)
def test_completed_requires_every_output_field(domain_sessions, job_context, field):
    values = completed_values() | {field: None}
    with domain_sessions() as session:
        session.add(make_job(job_context, **values))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


@pytest.mark.parametrize(
    "changes",
    [
        {"artifact_video_codec": "hevc"},
        {"artifact_audio_codec": "mp3"},
        {"artifact_fps_num": 24},
        {"artifact_fps_den": 0},
        {"artifact_duration_ms": 0},
        {"artifact_width": 0},
        {"artifact_height": -1},
        {"cancel_requested_at": utc_now()},
        {"error_code": "render_failed"},
    ],
)
def test_completed_rejects_wrong_output_or_cancel(domain_sessions, job_context, changes):
    with domain_sessions() as session:
        session.add(make_job(job_context, **(completed_values() | changes)))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_database_enforces_one_active_even_for_distinct_requests(domain_sessions, job_context):
    with domain_sessions() as session:
        first = make_job(job_context, idempotency_key=str(uuid4()))
        session.add(first)
        session.commit()
        session.add(make_job(job_context, profile="high", idempotency_key=str(uuid4())))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        first.status = first.stage = "failed"
        first.finished_at = utc_now()
        session.commit()
        session.add(make_job(job_context, profile="high", idempotency_key=str(uuid4())))
        session.commit()


def test_key_is_reserved_after_terminal_and_null_keys_can_repeat(domain_sessions, job_context):
    key = str(uuid4())
    with domain_sessions() as session:
        session.add(
            make_job(
                job_context,
                idempotency_key=key,
                status="failed",
                stage="failed",
                finished_at=utc_now(),
            )
        )
        session.commit()
        session.add(make_job(job_context, idempotency_key=key))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        for _ in range(2):
            session.add(
                make_job(job_context, status="failed", stage="failed", finished_at=utc_now())
            )
        session.commit()


def test_running_then_completed_accepts_real_metadata_shape(domain_sessions, job_context):
    with domain_sessions() as session:
        now = utc_now()
        job = make_job(
            job_context,
            status="running",
            stage="encoding",
            attempts=1,
            claim_token=uuid4(),
            lease_expires_at=now + timedelta(seconds=30),
            heartbeat_at=now,
        )
        session.add(job)
        session.commit()
        for key, value in completed_values().items():
            setattr(job, key, value)
        job.claim_token = job.lease_expires_at = None
        job.artifact_audio_codec = "aac"
        session.commit()


def test_migration_matches_model_and_is_additive(monkeypatch):
    migration = runpy.run_path(
        str(Path(__file__).parents[1] / "alembic/versions/0005_r05_render_exports.py")
    )
    assert migration["revision"] == "0005_r05_render_exports"
    assert migration["down_revision"] == "0004_r04_text_audio_previews"
    tables, indexes = [], []
    monkeypatch.setattr(
        migration["op"], "create_table", lambda *args, **kwargs: tables.append(args)
    )
    monkeypatch.setattr(
        migration["op"], "create_index", lambda name, *args, **kwargs: indexes.append(name)
    )
    migration["upgrade"]()
    assert len(tables) == 1 and tables[0][0] == "render_jobs"
    actual = {c.name: str(c.sqltext) for c in tables[0][1:] if isinstance(c, CheckConstraint)}
    expected = {
        c.name: str(c.sqltext)
        for c in RenderJob.__table__.constraints
        if isinstance(c, CheckConstraint)
    }
    assert actual == expected
    assert set(indexes) == {i.name for i in RenderJob.__table__.indexes}
    with pytest.raises(RuntimeError, match="preservation plan"):
        migration["downgrade"]()
