#!/usr/bin/env python3
"""Real R04.1 compatibility/MP3 probes; retain synthetic records and originals."""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.util.spec_from_file_location("r02", ROOT / "scripts/acceptance-r02.py")
assert loader and loader.loader
r02 = importlib.util.module_from_spec(loader)
loader.loader.exec_module(r02)
EVIDENCE = ROOT / "docs/r04/evidence"
SEED = EVIDENCE / "legacy-v1-seed.json"


def require(condition: object, message: str) -> None:
    r02.foundation.require(bool(condition), message)


def seed() -> None:
    require(not SEED.exists(), "Seed já existe; não criar outro projeto implicitamente.")
    r02.foundation.wait_ready()
    current = r02.foundation.compose("exec", "-T", "api", "alembic", "current")
    require("0003_r03_edit_spec_revisions" in current, "Seed deve anteceder a migração R04.")
    fixtures = json.loads(r02.foundation.execute_python("api", r02.FIXTURES))
    raw = base64.b64decode(fixtures["audio"])
    status, project = r02.json_request(
        "POST", "/api/v1/projects", {"name": "QA R04 compatibilidade v1", "target_preset": "16:9"}
    )
    require(status == 201, "Criação do projeto de QA falhou.")
    status, media = r02.upload(project["id"], raw)
    require(status == 201, "Upload da fonte sintética falhou.")
    plan = {
        "schema_version": 1,
        "source_media_id": media["id"],
        "trim": {"start_ms": 0, "end_ms": media["duration_ms"]},
        "canvas": {"preset": "16:9", "fit": "cover", "focal_x": 0.5, "focal_y": 0.5},
    }
    path = f"/api/v1/projects/{project['id']}/edit-spec"
    status, revision = r02.json_request("PUT", path, {**plan, "expected_revision": 0})
    require(status == 201 and revision["schema_version"] == 1, "Seed v1 não foi salvo.")
    result = {
        "generated_at": datetime.now(UTC).isoformat(),
        "created_before_migration": "0004_r04_text_audio_previews",
        "project_id": project["id"],
        "media": media,
        "revision": revision,
        "plan": plan,
        "original_sha256": hashlib.sha256(raw).hexdigest(),
        "storage": r02.storage_snapshot(project["id"]),
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    with SEED.open("x", encoding="utf-8") as target:
        json.dump(result, target, ensure_ascii=False, indent=2)
        target.write("\n")
    print(json.dumps({"seed": "passed", "project_id": project["id"], "revision": 1}))


def verify(fixtures: Path) -> None:
    stored = json.loads(SEED.read_text(encoding="utf-8"))
    project = stored["project_id"]
    path = f"/api/v1/projects/{project}/edit-spec"
    checks = []

    def passed(name: str, detail: object = None) -> None:
        checks.append({"check": name, "status": "passed", "detail": detail})
        print(json.dumps(checks[-1], ensure_ascii=False), flush=True)

    def error(result: tuple, status: int) -> None:
        actual, body = result
        require(actual == status, f"Esperado {status}, recebido {actual}.")
        require(set(body) == {"code", "message", "request_id"}, "Envelope divergente.")
        require(
            not any(s in json.dumps(body) for s in ("/data/", "/home/", "Traceback", "SELECT ")),
            "Erro não sanitizado.",
        )

    require(r02.request("GET", path) == (200, stored["revision"]), "GET v1 mudou após migração.")
    require(r02.storage_snapshot(project) == stored["storage"], "Original v1 mudou.")
    same = r02.json_request("PUT", path, {**stored["plan"], "expected_revision": 1})
    require(same == (200, stored["revision"]), "No-op v1 mudou após migração.")
    error(r02.json_request("PUT", path, {**stored["plan"], "expected_revision": 0}), 409)
    passed("pre_migration_v1_get_and_noop_unchanged_stale_conflict_409")

    music = (fixtures / "music-short.mp3").read_bytes()
    body, headers = r02.multipart(music, "../../musica.MP3")
    status, audio = r02.request("POST", f"/api/v1/projects/{project}/audio", body, headers)
    require(status == 201, f"MP3 válido falhou: {status}.")
    require(audio["checksum_sha256"] == hashlib.sha256(music).hexdigest(), "SHA-256 divergente.")
    require(audio["byte_size"] == len(music) and audio["codec"] == "mp3", "Metadados incorretos.")
    require(audio["original_filename"] == "musica.MP3", "Nome não saneado.")
    require("storage_key" not in audio, "Chave privada exposta.")
    require(
        r02.request("GET", f"/api/v1/projects/{project}/audio/{audio['id']}") == (200, audio),
        "Detalhe MP3 divergente.",
    )
    stream = (body[i : i + 1024] for i in range(0, len(body), 1024))
    status, duplicate = r02.request(
        "POST", f"/api/v1/projects/{project}/audio", stream, headers, chunked=True
    )
    require(status == 201 and duplicate["id"] != audio["id"], "MP3 chunked/sobrescrita falhou.")
    require(duplicate["checksum_sha256"] == audio["checksum_sha256"], "Duplicação mudou bytes.")
    passed("real_mp3_upload_mime_independent_chunked_sanitized_name_sha256", audio)

    invalid = [
        (b"", "empty.mp3", 422),
        (b"not an mp3", "fake.mp3", 415),
        (music[:120], "truncated.mp3", 422),
        (music, "wrong.mp4", 415),
    ]
    for raw, filename, expected in invalid:
        payload, content = r02.multipart(raw, filename)
        error(r02.request("POST", f"/api/v1/projects/{project}/audio", payload, content), expected)
    passed("empty_truncated_422_fake_and_extension_415")
    # Real server rejection before multipart is parsed; do not send a huge body.
    error(
        r02.request(
            "POST",
            f"/api/v1/projects/{project}/audio",
            b"",
            {"Content-Length": str(31457280 + 65536 + 1)},
        ),
        413,
    )
    passed("mp3_own_body_limit_413_before_parsing")
    version2 = {
        **copy.deepcopy(stored["plan"]),
        "schema_version": 2,
        "text_overlays": [
            {
                "id": str(uuid4()),
                "text": 'Edivo: ação "real" \\ teste',
                "start_ms": 0,
                "end_ms": 900,
                "position": "top",
                "style": "white_outline",
                "size": "medium",
            }
        ],
        "audio": {
            "source_volume": 1.25,
            "background_audio_id": audio["id"],
            "background_volume": 0.15,
            "background_loop": True,
            "fade_in_ms": 100,
            "fade_out_ms": 100,
        },
    }
    status, revision = r02.json_request("PUT", path, {**version2, "expected_revision": 1})
    require(status == 200 and revision["revision_number"] == 2, "Persistência v2 falhou.")
    require(r02.request("GET", path) == (200, revision), "GET v2 divergente.")
    require(
        r02.json_request("PUT", path, {**version2, "expected_revision": 2}) == (200, revision),
        "No-op v2 criou revisão.",
    )
    error(r02.json_request("PUT", path, {**version2, "expected_revision": 1}), 409)
    passed("v2_text_unicode_literal_audio_persisted_noop_and_conflict", revision)
    output = {
        "status": "passed",
        "stage": "R04.1",
        "generated_at": datetime.now(UTC).isoformat(),
        "checks": checks,
        "project_id": project,
        "latest_revision": revision,
        "audio_ids": [audio["id"], duplicate["id"]],
        "data_policy": "Somente novos dados sintéticos; originais e revisões preservados.",
    }
    (EVIDENCE / "edit-spec-audio.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def inspect_storage() -> None:
    stored = json.loads(SEED.read_text(encoding="utf-8"))
    project_id = stored["project_id"]
    diagnostic = """
import hashlib,json
from uuid import UUID
from sqlalchemy import select,text
from sqlalchemy.exc import SQLAlchemyError
from app.core.config import Settings
from app.db.session import Database
from app.models.audio_asset import AudioAsset
from app.storage.local import LocalStorage
settings=Settings();db=Database(settings);storage=LocalStorage(settings)
project_id=UUID(PROJECT_ID)
result={'audio':[],'immutability':{}}
try:
    with db.sessions() as session:
        for asset in session.scalars(select(AudioAsset).where(AudioAsset.project_id==project_id)):
            opened=storage.open_audio(asset.storage_key,project_id,asset.id,asset.byte_size)
            try:
                digest=hashlib.sha256()
                while chunk:=opened.file.read(1048576):digest.update(chunk)
                info=opened.stat_result
                result['audio'].append({'id':str(asset.id),'sha256':digest.hexdigest(),
                    'sha256_matches':digest.hexdigest()==asset.checksum_sha256,
                    'bytes':info.st_size,'mode':oct(info.st_mode&0o777)})
            finally:opened.file.close()
    for operation,query in (
        ('update','UPDATE edit_spec_revisions SET spec_json=spec_json WHERE project_id=:id'),
        ('delete','DELETE FROM edit_spec_revisions WHERE project_id=:id')):
        with db.engine.connect() as connection:
            transaction=connection.begin()
            try:
                connection.execute(text(query),{'id':project_id})
            except SQLAlchemyError as error:
                result['immutability'][operation]=getattr(error.orig,'sqlstate',None)
            else:result['immutability'][operation]='NOT_BLOCKED'
            finally:transaction.rollback()
    with db.engine.connect() as connection:
        rows=connection.execute(text('''SELECT id,schema_version,spec_json
            FROM edit_spec_revisions WHERE project_id=:id ORDER BY revision_number'''),
            {'id':project_id}).mappings().all()
        result['revisions']=[{'id':str(row['id']),'schema_version':row['schema_version'],
            'spec_json':row['spec_json']} for row in rows]
    result['staging_files']=sum(1 for _ in (settings.storage_root/'temp').iterdir())
    print(json.dumps(result))
finally:db.close()
""".replace("PROJECT_ID", repr(project_id))
    result = json.loads(r02.foundation.execute_python("api", diagnostic))
    require(result["audio"], "Nenhum MP3 importado para inspeção.")
    require(
        all(item["mode"] == "0o400" and item["sha256_matches"] for item in result["audio"]),
        "MP3 perdeu integridade ou permissão 0400.",
    )
    require(result["immutability"] == {"update": "55000", "delete": "55000"}, "Trigger mudou.")
    require(result["revisions"][0]["spec_json"] == stored["plan"], "Snapshot v1 mudou.")
    require(result["staging_files"] == 0, "Temporários não foram limpos.")
    result.update(status="passed", generated_at=datetime.now(UTC).isoformat())
    (EVIDENCE / "audio-storage-database.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"storage_database": "passed", "audio_count": len(result["audio"])}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-v1", action="store_true")
    parser.add_argument("--inspect-storage", action="store_true")
    parser.add_argument("--fixtures", type=Path, default=Path("/tmp/edivo-r04/fixtures"))
    options = parser.parse_args()
    if options.seed_v1:
        seed()
    elif options.inspect_storage:
        inspect_storage()
    else:
        verify(options.fixtures)
