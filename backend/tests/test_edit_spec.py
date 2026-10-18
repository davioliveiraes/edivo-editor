"""Isolated API/domain checks; PostgreSQL locking and triggers have a real QA harness."""

import copy
import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.models.edit_spec_revision import EditSpecRevision
from app.models.media_asset import MediaAsset
from app.models.project import Project
from app.repositories.projects import ProjectRepository

PROJECTS = "/api/v1/projects"


def plan(source_media_id, *, expected_revision=0, start_ms=0, end_ms=1000):
    return {
        "expected_revision": expected_revision,
        "schema_version": 1,
        "source_media_id": str(source_media_id),
        "trim": {"start_ms": start_ms, "end_ms": end_ms},
        "canvas": {"preset": "9:16", "fit": "cover", "focal_x": 0.5, "focal_y": 0.5},
    }


def seed_source(sessions, project_id, *, duration_ms=1000):
    identifier = uuid4()
    with sessions() as session:
        source = MediaAsset(
            id=identifier,
            project_id=UUID(str(project_id)),
            kind="source_video",
            original_filename="synthetic.mp4",
            storage_key=f"originals/{project_id}/{identifier}/source.mp4",
            checksum_sha256="a" * 64,
            byte_size=100,
            mime_detected="video/mp4",
            duration_ms=duration_ms,
            width=320,
            height=180,
            fps_num=30000,
            fps_den=1001,
            video_codec="h264",
            has_audio=False,
            audio_codec=None,
            probe_metadata={"duration_ms": duration_ms},
        )
        session.add(source)
        session.commit()
    return identifier


@pytest.fixture
def edit_context(domain_client, domain_sessions):
    project = domain_client.post(
        PROJECTS, json={"name": "Editor sintético", "target_preset": "1:1"}
    ).json()
    source_id = seed_source(domain_sessions, project["id"])
    return project, source_id, f"{PROJECTS}/{project['id']}/edit-spec"


def revision_count(sessions):
    with sessions() as session:
        return session.scalar(select(func.count()).select_from(EditSpecRevision))


def assert_safe_error(response, code, status):
    assert response.status_code == status, response.text
    assert response.json()["code"] == code
    assert set(response.json()) == {"code", "message", "request_id"}
    assert response.json()["request_id"] == response.headers["x-request-id"]
    for private in ("/home/", "postgresql", "Traceback", "password=", "private"):
        assert private not in response.text


def test_missing_edit_spec_distinguished_from_missing_project(domain_client, edit_context):
    _, _, path = edit_context
    assert_safe_error(domain_client.get(path), "edit_spec_not_found", 404)
    assert_safe_error(
        domain_client.get(f"{PROJECTS}/{uuid4()}/edit-spec"), "project_not_found", 404
    )


def test_first_second_get_immutable_revision_and_original_metadata(
    domain_client, domain_sessions, edit_context
):
    project, source_id, path = edit_context
    payload = plan(source_id)
    first_response = domain_client.put(path, json=payload)
    assert first_response.status_code == 201, first_response.text
    first = first_response.json()
    assert set(first) == {
        "id",
        "project_id",
        "revision_number",
        "created_at",
        "schema_version",
        "source_media_id",
        "trim",
        "canvas",
    }
    assert first["revision_number"] == 1
    assert first["source_media_id"] == str(source_id)
    assert first["project_id"] == project["id"]
    assert first["created_at"].endswith("Z")
    assert domain_client.get(path).json() == first

    second_payload = plan(source_id, expected_revision=1, start_ms=150, end_ms=800)
    second_payload["canvas"] = {
        "preset": "4:5",
        "fit": "contain",
        "focal_x": 0.2,
        "focal_y": 0.8,
    }
    second_response = domain_client.put(path, json=second_payload)
    assert second_response.status_code == 200
    second = second_response.json()
    assert second["revision_number"] == 2 and second["id"] != first["id"]
    assert second["canvas"]["focal_x"] == 0.2  # contain preserves a future cover focus.
    assert domain_client.get(path).json() == second
    with domain_sessions() as session:
        snapshots = list(
            session.scalars(select(EditSpecRevision).order_by(EditSpecRevision.revision_number))
        )
        assert len(snapshots) == 2
        assert snapshots[0].spec_json == {
            key: value for key, value in payload.items() if key != "expected_revision"
        }
        assert snapshots[0].source_media_id == source_id
        assert session.get(Project, UUID(project["id"])).target_preset == "1:1"
        source = session.get(MediaAsset, source_id)
        assert source.byte_size == 100 and source.checksum_sha256 == "a" * 64


def test_identical_save_noop_keeps_id_number_and_timestamp(
    domain_client, domain_sessions, edit_context
):
    _, source_id, path = edit_context
    first = domain_client.put(path, json=plan(source_id)).json()
    repeated = domain_client.put(path, json=plan(source_id, expected_revision=1))
    assert repeated.status_code == 200
    assert repeated.json() == first
    assert revision_count(domain_sessions) == 1
    stale = domain_client.put(path, json=plan(source_id, expected_revision=0))
    assert_safe_error(stale, "edit_spec_conflict", 409)
    assert domain_client.get(path).json() == first


@pytest.mark.parametrize("expected", [1, 7, 999999999999999999999999])
def test_first_save_requires_expected_zero(domain_client, domain_sessions, edit_context, expected):
    _, source_id, path = edit_context
    response = domain_client.put(path, json=plan(source_id, expected_revision=expected))
    assert_safe_error(response, "edit_spec_conflict", 409)
    assert revision_count(domain_sessions) == 0


def test_stale_update_never_overwrites_latest(domain_client, domain_sessions, edit_context):
    _, source_id, path = edit_context
    domain_client.put(path, json=plan(source_id))
    second = domain_client.put(path, json=plan(source_id, expected_revision=1, end_ms=800)).json()
    response = domain_client.put(path, json=plan(source_id, expected_revision=1, end_ms=700))
    assert_safe_error(response, "edit_spec_conflict", 409)
    assert domain_client.get(path).json() == second
    assert revision_count(domain_sessions) == 2


@pytest.mark.parametrize("preset", ["9:16", "1:1", "4:5", "16:9"])
@pytest.mark.parametrize("fit", ["cover", "contain"])
def test_four_presets_fit_and_focal_boundaries(domain_client, edit_context, preset, fit):
    _, source_id, path = edit_context
    payload = plan(source_id)
    payload["canvas"].update(preset=preset, fit=fit, focal_x=0, focal_y=1)
    response = domain_client.put(path, json=payload)
    assert response.status_code == 201
    assert response.json()["canvas"] == payload["canvas"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_revision", True),
        ("expected_revision", "0"),
        ("expected_revision", 0.0),
        ("expected_revision", -1),
        ("schema_version", True),
        ("schema_version", "1"),
        ("schema_version", 1.0),
        ("schema_version", 0),
        ("schema_version", 2),
        ("source_media_id", False),
        ("source_media_id", "not-a-uuid"),
        ("source_media_id", None),
        ("unknown", "private"),
        ("trim.start_ms", True),
        ("trim.start_ms", "0"),
        ("trim.start_ms", 0.0),
        ("trim.start_ms", -1),
        ("trim.end_ms", True),
        ("trim.end_ms", "1000"),
        ("trim.end_ms", 1000.0),
        ("trim.end_ms", 0),
        ("trim.extra", 10),
        ("canvas.preset", "4K"),
        ("canvas.preset", None),
        ("canvas.fit", "stretch"),
        ("canvas.focal_x", -0.01),
        ("canvas.focal_y", 1.01),
        ("canvas.focal_x", True),
        ("canvas.focal_y", "0.5"),
        ("canvas.focal_x", None),
        ("canvas.extra", "private"),
    ],
)
def test_strict_payload_validation_no_rows(
    domain_client, domain_sessions, edit_context, field, value
):
    _, source_id, path = edit_context
    payload = plan(source_id)
    if "." in field:
        parent, child = field.split(".")
        payload[parent][child] = value
    else:
        payload[field] = value
    response = domain_client.put(path, json=payload)
    assert_safe_error(response, "validation_error", 422)
    assert revision_count(domain_sessions) == 0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_focus_rejected(domain_client, edit_context, value):
    _, source_id, path = edit_context
    payload = plan(source_id)
    payload["canvas"]["focal_x"] = value
    response = domain_client.put(
        path, content=json.dumps(payload), headers={"Content-Type": "application/json"}
    )
    assert_safe_error(response, "validation_error", 422)


@pytest.mark.parametrize("missing", ["expected_revision", "schema_version", "trim", "canvas"])
def test_complete_plan_required(domain_client, edit_context, missing):
    _, source_id, path = edit_context
    payload = plan(source_id)
    del payload[missing]
    assert_safe_error(domain_client.put(path, json=payload), "validation_error", 422)


@pytest.mark.parametrize(
    "start_ms,end_ms,code",
    [
        (900, 800, "validation_error"),
        (0, 1001, "invalid_edit_spec"),
        (0, 99, "invalid_edit_spec"),
        (999, 1000, "invalid_edit_spec"),
    ],
)
def test_trim_bounds_and_minimum(domain_client, edit_context, start_ms, end_ms, code):
    _, source_id, path = edit_context
    response = domain_client.put(path, json=plan(source_id, start_ms=start_ms, end_ms=end_ms))
    assert_safe_error(response, code, 422)


@pytest.mark.parametrize("duration_ms", [1, 50, 99, 100, 101])
def test_short_sources_accept_full_duration_and_exact_minimum(
    domain_client, domain_sessions, edit_context, duration_ms
):
    project, _, path = edit_context
    source_id = seed_source(domain_sessions, project["id"], duration_ms=duration_ms)
    response = domain_client.put(path, json=plan(source_id, end_ms=duration_ms))
    assert response.status_code == 201, response.text
    rejected = domain_client.put(
        path, json=plan(source_id, expected_revision=1, start_ms=1, end_ms=duration_ms)
    )
    assert rejected.status_code == (200 if duration_ms == 101 else 422)


def test_source_must_exist_and_belong_to_project(domain_client, domain_sessions, edit_context):
    _, _, path = edit_context
    other_project = domain_client.post(PROJECTS, json={"name": "Outro"}).json()
    other_source = seed_source(domain_sessions, other_project["id"])
    for source_id in (uuid4(), other_source):
        assert_safe_error(domain_client.put(path, json=plan(source_id)), "media_not_found", 404)
    assert revision_count(domain_sessions) == 0


def test_deleted_project_blocks_read_and_save_preserving_revisions(
    domain_client, domain_sessions, edit_context
):
    project, source_id, path = edit_context
    domain_client.put(path, json=plan(source_id))
    assert domain_client.delete(f"{PROJECTS}/{project['id']}").status_code == 204
    assert_safe_error(domain_client.get(path), "project_not_found", 404)
    assert_safe_error(
        domain_client.put(path, json=plan(source_id, expected_revision=1)), "project_not_found", 404
    )
    assert revision_count(domain_sessions) == 1


def test_project_preset_changes_do_not_modify_saved_plan(domain_client, edit_context):
    project, source_id, path = edit_context
    saved = domain_client.put(path, json=plan(source_id)).json()
    domain_client.patch(f"{PROJECTS}/{project['id']}", json={"target_preset": "16:9"})
    assert domain_client.get(path).json() == saved
    assert saved["canvas"]["preset"] == "9:16"


def test_project_lock_precedes_current_revision_lookup(domain_client, edit_context, monkeypatch):
    _, source_id, path = edit_context
    calls = []
    original = ProjectRepository.get_active

    def get_active(repository, identifier, *, for_update=False):
        calls.append(for_update)
        return original(repository, identifier, for_update=for_update)

    monkeypatch.setattr(ProjectRepository, "get_active", get_active)
    assert domain_client.put(path, json=plan(source_id)).status_code == 201
    assert calls == [True]


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_orm_revisions_cannot_be_mutated(domain_client, domain_sessions, edit_context, operation):
    _, source_id, path = edit_context
    saved = domain_client.put(path, json=plan(source_id)).json()
    with domain_sessions() as session:
        revision = session.get(EditSpecRevision, UUID(saved["id"]))
        if operation == "delete":
            session.delete(revision)
        else:
            revision.spec_json = copy.deepcopy(revision.spec_json)
            revision.spec_json["trim"]["end_ms"] = 500
        with pytest.raises(ValueError, match="edit_spec_revision_immutable"):
            session.commit()
        session.rollback()
    assert domain_client.get(path).json() == saved


@pytest.mark.parametrize("invalid", ["zero_revision", "schema", "foreign_key", "duplicate"])
def test_revision_database_constraints(domain_client, domain_sessions, edit_context, invalid):
    project, source_id, path = edit_context
    domain_client.put(path, json=plan(source_id))
    with domain_sessions() as session:
        revision = EditSpecRevision(
            project_id=UUID(project["id"]),
            source_media_id=uuid4() if invalid == "foreign_key" else source_id,
            revision_number=0
            if invalid == "zero_revision"
            else (1 if invalid == "duplicate" else 2),
            schema_version=2 if invalid == "schema" else 1,
            spec_json={
                key: value for key, value in plan(source_id).items() if key != "expected_revision"
            },
        )
        session.add(revision)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
    assert revision_count(domain_sessions) == 1


def test_database_commit_failure_sanitized_and_rolled_back(
    domain_client, domain_sessions, edit_context, monkeypatch
):
    _, source_id, path = edit_context

    def fail(_session):
        raise OperationalError("private SQL", {}, RuntimeError("password=secret"))

    monkeypatch.setattr(Session, "commit", fail)
    assert_safe_error(domain_client.put(path, json=plan(source_id)), "database_unavailable", 503)
    assert revision_count(domain_sessions) == 0


def test_edit_spec_cors_allows_put_only_for_local_origins(domain_client, edit_context):
    _, _, path = edit_context
    allowed = domain_client.options(
        path,
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert allowed.status_code == 200
    assert "PUT" in allowed.headers["access-control-allow-methods"]
    denied = domain_client.options(
        path,
        headers={
            "Origin": "https://external.example",
            "Access-Control-Request-Method": "PUT",
        },
    )
    assert denied.status_code == 400
