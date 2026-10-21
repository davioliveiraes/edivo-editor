"""R04 contracts, plus v1 preservation through the unchanged revision service."""

import json
from uuid import UUID, uuid4

import pytest
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from test_edit_spec import plan, seed_source

from app.models.audio_asset import AudioAsset
from app.models.edit_spec_revision import EditSpecRevision
from app.schemas.edit_spec import AudioSpec, EditSpecPut, EditSpecV1Put, EditSpecV2Put


def v2_plan(source_id, expected_revision=0):
    data = plan(source_id, expected_revision=expected_revision)
    data.update(
        schema_version=2,
        text_overlays=[
            {
                "id": str(uuid4()),
                "text": "  Próximo: 'vídeo' \\ edição\nVamos!  ",
                "start_ms": 0,
                "end_ms": 1000,
                "position": "bottom",
                "style": "blue_outline",
                "size": "medium",
            }
        ],
        audio={
            "source_volume": 1,
            "background_volume": 0.15,
            "background_loop": False,
            "background_audio_id": None,
            "fade_in_ms": 100,
            "fade_out_ms": 100,
        },
    )
    return data


def seed_audio(sessions, project_id):
    identifier = uuid4()
    with sessions() as session:
        session.add(
            AudioAsset(
                id=identifier,
                project_id=UUID(str(project_id)),
                original_filename="Música.mp3",
                storage_key=f"originals-audio/{project_id}/{identifier}/source.mp3",
                checksum_sha256="a" * 64,
                byte_size=1000,
                mime_detected="audio/mpeg",
                duration_ms=2000,
                codec="mp3",
                sample_rate=44100,
                channels=2,
            )
        )
        session.commit()
    return identifier


@pytest.fixture
def context(domain_client, domain_sessions):
    project = domain_client.post("/api/v1/projects", json={"name": "R04"}).json()
    source = seed_source(domain_sessions, project["id"])
    return project, source, f"/api/v1/projects/{project['id']}/edit-spec"


def test_v1_get_unchanged_v2_save_and_noop_conflict(domain_client, domain_sessions, context):
    project, source, path = context
    first = domain_client.put(path, json=plan(source)).json()
    assert first["schema_version"] == 1
    assert "text_overlays" not in first and "audio" not in first
    assert domain_client.get(path).json() == first
    data = v2_plan(source, 1)
    data["audio"]["background_audio_id"] = str(seed_audio(domain_sessions, project["id"]))
    saved = domain_client.put(path, json=data)
    assert saved.status_code == 200, saved.text
    snapshot = saved.json()
    assert snapshot["schema_version"] == 2 and snapshot["revision_number"] == 2
    assert snapshot["text_overlays"][0]["text"] == "Próximo: 'vídeo' \\ edição\nVamos!"
    assert "expected_revision" not in snapshot
    assert domain_client.get(path).json() == snapshot
    data["expected_revision"] = 2
    assert domain_client.put(path, json=data).json() == snapshot
    data["expected_revision"] = 1
    assert domain_client.put(path, json=data).status_code == 409
    with domain_sessions() as session:
        revisions = list(
            session.scalars(select(EditSpecRevision).order_by(EditSpecRevision.revision_number))
        )
        assert len(revisions) == 2
        assert revisions[0].spec_json == {
            k: v for k, v in plan(source).items() if k != "expected_revision"
        }


def test_discriminated_put_plan_and_audio_defaults():
    adapter = TypeAdapter(EditSpecPut)
    old = adapter.validate_python(plan(uuid4()))
    assert isinstance(old, EditSpecV1Put) and old.plan().schema_version == 1
    payload = v2_plan(uuid4())
    del payload["audio"]
    del payload["text_overlays"]
    new = adapter.validate_python(payload)
    assert isinstance(new, EditSpecV2Put)
    assert new.audio == AudioSpec() and new.text_overlays == []
    assert "expected_revision" not in new.plan().model_dump()


@pytest.mark.parametrize("version", [True, False, 1.0, 2.0, "1", "2", 0, 3, None])
def test_strict_version_for_both_union_branches(version):
    payload = v2_plan(uuid4())
    payload["schema_version"] = version
    with pytest.raises(ValidationError):
        TypeAdapter(EditSpecPut).validate_python(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("text", ""),
        ("text", " \n "),
        ("text", "x" * 121),
        ("text", "a\nb\nc"),
        ("text", "<b>Título</b>"),
        ("text", "%{localtime}"),
        ("text", "A\u200bB"),
        ("text", "A\u2028B"),
        ("text", "A\u2029B"),
        ("text", "A\x00B"),
        ("text", "A\tB"),
        ("text", "A\u202eB"),
        ("text", "\rA"),
        ("text", 5),
        ("start_ms", True),
        ("end_ms", 1000.0),
        ("start_ms", -1),
        ("start_ms", 1000),
        ("end_ms", 1001),
        ("position", "left"),
        ("style", "drawtext=evil"),
        ("size", "42"),
        ("fontfile", "/private/font.ttf"),
    ],
)
def test_invalid_overlay_is_rejected(domain_client, context, field, value):
    _, source, path = context
    data = v2_plan(source)
    data["text_overlays"][0][field] = value
    response = domain_client.put(path, json=data)
    assert response.status_code == 422 and response.json()["code"] == "validation_error"


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_volume", -0.1),
        ("source_volume", 2.1),
        ("source_volume", True),
        ("source_volume", "1"),
        ("background_volume", -0.01),
        ("background_volume", 1.01),
        ("background_loop", 1),
        ("background_loop", "false"),
        ("fade_in_ms", True),
        ("fade_in_ms", 1.0),
        ("fade_out_ms", 3001),
        ("fade_in_ms", -1),
        ("fade_in_ms", 1000),
        ("filter", "volume=1"),
    ],
)
def test_invalid_audio_is_rejected(domain_client, context, field, value):
    _, source, path = context
    data = v2_plan(source)
    data["audio"][field] = value
    assert domain_client.put(path, json=data).status_code == 422


@pytest.mark.parametrize("field", ["source_volume", "background_volume"])
@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_nonfinite_audio_rejected(domain_client, context, field, value):
    _, source, path = context
    data = v2_plan(source)
    data["audio"][field] = value
    assert (
        domain_client.put(
            path, content=json.dumps(data), headers={"Content-Type": "application/json"}
        ).status_code
        == 422
    )


@pytest.mark.parametrize("invalid", ["duplicate", "four", "trim"])
def test_overlay_collection_and_changed_trim_validation(domain_client, context, invalid):
    _, source, path = context
    data = v2_plan(source)
    if invalid == "duplicate":
        data["text_overlays"] *= 2
    elif invalid == "four":
        data["text_overlays"] = [dict(data["text_overlays"][0], id=str(uuid4())) for _ in range(4)]
    else:
        data["trim"]["start_ms"] = 100
    assert domain_client.put(path, json=data).status_code == 422


def test_music_must_belong_to_same_active_project(domain_client, domain_sessions, context):
    _, source, path = context
    other = domain_client.post("/api/v1/projects", json={"name": "Outro"}).json()
    other_audio = seed_audio(domain_sessions, other["id"])
    for audio in (other_audio, uuid4()):
        data = v2_plan(source)
        data["audio"]["background_audio_id"] = str(audio)
        response = domain_client.put(path, json=data)
        assert response.status_code == 404 and response.json()["code"] == "audio_not_found"


def test_three_texts_boundary_values_and_silent_source(domain_client, context):
    _, source, path = context
    data = v2_plan(source)
    data["audio"].update(source_volume=2, background_volume=1, fade_in_ms=500, fade_out_ms=500)
    data["text_overlays"] = [
        dict(data["text_overlays"][0], id=str(uuid4()), position=position)
        for position in ("top", "center", "bottom")
    ]
    assert domain_client.put(path, json=data).status_code == 201
