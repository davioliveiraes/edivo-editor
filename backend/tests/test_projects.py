from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError

from app.models.media_asset import MediaAsset
from app.models.project import Project

PROJECTS = "/api/v1/projects"


def create_project(client, **changes):
    response = client.post(PROJECTS, json={"name": "Vídeo da campanha", **changes})
    assert response.status_code == 201
    return response.json()


def asset_for(project_id):
    return MediaAsset(
        project_id=UUID(project_id),
        original_filename="sintetico.mp4",
        storage_key=f"originals/{project_id}/{uuid4()}/source.mp4",
        checksum_sha256="a" * 64,
        byte_size=100,
        mime_detected="video/mp4",
        duration_ms=1000,
        width=320,
        height=180,
        fps_num=30000,
        fps_den=1001,
        video_codec="h264",
        has_audio=False,
        audio_codec=None,
        probe_metadata={"format": {"duration_ms": 1000}},
    )


def test_project_crud_and_soft_delete(domain_client, domain_sessions):
    project = create_project(
        domain_client, name="  Campanha Outubro  ", purpose="  WhatsApp  ", target_preset="9:16"
    )
    project_id = project["id"]
    assert project["name"] == "Campanha Outubro"
    assert project["purpose"] == "WhatsApp"
    assert project["target_preset"] == "9:16"
    assert project["created_at"].endswith("Z")
    assert "deleted_at" not in project
    assert domain_client.get(f"{PROJECTS}/{project_id}").json() == project

    updated = domain_client.patch(
        f"{PROJECTS}/{project_id}", json={"name": "  Novo nome  ", "purpose": None}
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Novo nome"
    assert updated.json()["purpose"] is None
    assert updated.json()["target_preset"] == "9:16"
    assert updated.json()["updated_at"] >= project["updated_at"]

    with domain_sessions() as session:
        asset = asset_for(project_id)
        session.add(asset)
        session.commit()
        asset_id = asset.id
    deleted = domain_client.delete(f"{PROJECTS}/{project_id}")
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert domain_client.get(PROJECTS).json()["items"] == []
    for method in ("get", "delete", "patch"):
        kwargs = {"json": {"name": "Reviver"}} if method == "patch" else {}
        response = getattr(domain_client, method)(f"{PROJECTS}/{project_id}", **kwargs)
        assert response.status_code == 404
        assert response.json()["code"] == "project_not_found"
    with domain_sessions() as session:
        stored = session.get(Project, UUID(project_id))
        assert stored is not None and stored.deleted_at is not None
        assert session.get(MediaAsset, asset_id) is not None


def test_projects_empty_page_and_nonunique_name(domain_client):
    assert domain_client.get(PROJECTS).json() == {"items": [], "total": 0, "limit": 20, "offset": 0}
    first = create_project(domain_client)
    second = create_project(domain_client)
    assert first["id"] != second["id"]
    assert domain_client.get(PROJECTS).json()["total"] == 2


def test_projects_pagination_deterministic_order(domain_client, domain_sessions):
    projects = [create_project(domain_client, name=f"Projeto {index}") for index in range(4)]
    same_time = datetime(2026, 10, 17, tzinfo=UTC)
    with domain_sessions() as session:
        for project in session.scalars(select(Project)):
            project.created_at = same_time
        session.commit()
    expected = sorted((project["id"] for project in projects), reverse=True)
    first = domain_client.get(PROJECTS, params={"limit": 2, "offset": 0}).json()
    second = domain_client.get(PROJECTS, params={"limit": 2, "offset": 2}).json()
    assert first["total"] == second["total"] == 4
    assert first["limit"] == 2 and second["offset"] == 2
    assert [project["id"] for project in first["items"] + second["items"]] == expected
    assert domain_client.get(PROJECTS, params={"offset": 100}).json()["items"] == []


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"name": ""},
        {"name": "   "},
        {"name": None},
        {"name": 123},
        {"name": "x" * 121},
        {"name": "ok", "purpose": "x" * 501},
        {"name": "ok", "target_preset": "4K"},
        {"name": "ok", "deleted_at": "now"},
    ],
)
def test_project_creation_rejects_invalid_data(domain_client, domain_sessions, payload):
    response = domain_client.post(PROJECTS, json=payload)
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    with domain_sessions() as session:
        assert session.scalar(select(func.count()).select_from(Project)) == 0


@pytest.mark.parametrize(
    "payload", [{}, {"name": None}, {"name": " "}, {"id": str(uuid4())}, {"purpose": "x" * 501}]
)
def test_project_patch_rejects_invalid_data(domain_client, payload):
    project = create_project(domain_client)
    response = domain_client.patch(f"{PROJECTS}/{project['id']}", json=payload)
    assert response.status_code == 422
    assert domain_client.get(f"{PROJECTS}/{project['id']}").json()["name"] == project["name"]


@pytest.mark.parametrize("preset", [None, "9:16", "1:1", "4:5", "16:9"])
def test_project_target_presets_and_field_limits(domain_client, preset):
    project = create_project(domain_client, name="N" * 120, purpose="P" * 500, target_preset=preset)
    assert project["target_preset"] == preset
    response = domain_client.patch(f"{PROJECTS}/{project['id']}", json={"target_preset": None})
    assert response.status_code == 200
    assert response.json()["target_preset"] is None


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"limit": "x"}])
def test_pagination_rejects_invalid_values(domain_client, params):
    assert domain_client.get(PROJECTS, params=params).status_code == 422


@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_project_unknown_uuid_and_invalid_uuid(domain_client, method):
    kwargs = {"json": {"name": "Novo"}} if method == "patch" else {}
    assert getattr(domain_client, method)(f"{PROJECTS}/{uuid4()}", **kwargs).status_code == 404
    response = getattr(domain_client, method)(f"{PROJECTS}/not-a-uuid", **kwargs)
    assert response.status_code == 422
    assert "not-a-uuid" not in response.text


def test_database_failure_is_sanitized_503(domain_client, domain_application, monkeypatch):
    def fail_session():
        raise OperationalError("private SQL /home/private", {}, RuntimeError("password=secret"))

    monkeypatch.setattr(domain_application.state.database, "sessions", fail_session)
    response = domain_client.post(PROJECTS, json={"name": "Novo"})
    assert response.status_code == 503
    assert response.json() == {
        "code": "database_unavailable",
        "message": "Banco de dados indisponível. Tente novamente.",
        "request_id": response.headers["x-request-id"],
    }
    assert "private" not in response.text and "secret" not in response.text
    assert domain_client.get("/api/v1/system/health").status_code == 200


def test_commit_failure_rolls_back_project(domain_client, domain_sessions, monkeypatch):
    from sqlalchemy.orm import Session

    def fail_commit(self):
        self.flush()
        raise OperationalError("private SQL", {}, RuntimeError("private"))

    monkeypatch.setattr(Session, "commit", fail_commit)
    response = domain_client.post(PROJECTS, json={"name": "Não persistido"})
    assert response.status_code == 503
    with domain_sessions() as session:
        assert session.scalar(select(func.count()).select_from(Project)) == 0


def test_database_constraints_foreign_key_and_positive_media(domain_sessions):
    with domain_sessions() as session:
        asset = asset_for(str(uuid4()))
        session.add(asset)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        project = Project(name="Projeto")
        session.add(project)
        session.commit()
        asset = asset_for(str(project.id))
        asset.byte_size = 0
        session.add(asset)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_cors_allows_project_mutations_only_for_local_origins(domain_client):
    response = domain_client.options(
        PROJECTS,
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert "POST" in response.headers["access-control-allow-methods"]
    response = domain_client.options(
        PROJECTS,
        headers={"Origin": "https://external.example", "Access-Control-Request-Method": "DELETE"},
    )
    assert response.status_code == 400
