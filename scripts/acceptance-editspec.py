#!/usr/bin/env python3
"""Exercise R03 EditSpec with real HTTP/PostgreSQL and synthetic R02 imports.

Only Edivo db/api/worker are restarted. QA projects are logically deleted and
their originals/revisions retained. All direct write/constraint probes roll
back, including in the unexpected case that an immutability trigger is absent.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import importlib.util
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
module_spec = importlib.util.spec_from_file_location("r02", ROOT / "scripts/acceptance-r02.py")
assert module_spec and module_spec.loader
r02 = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(r02)
foundation = r02.foundation


def revision_snapshot(project_id: str) -> list[dict]:
    return json.loads(
        foundation.execute_python(
            "api",
            f"""
import json
from uuid import UUID
from sqlalchemy import text
from app.core.config import Settings
from app.db.session import Database
db=Database(Settings())
try:
    with db.engine.connect() as connection:
        rows=connection.execute(text('''
            SELECT id,project_id,revision_number,source_media_id,schema_version,
                   spec_json,created_at
            FROM edit_spec_revisions WHERE project_id=:project ORDER BY revision_number
        '''),{{'project':UUID({project_id!r})}}).mappings()
        result=[]
        for row in rows:
            item=dict(row)
            for name in ('id','project_id','source_media_id'): item[name]=str(item[name])
            item['created_at']=item['created_at'].isoformat()
            result.append(item)
    print(json.dumps(result))
finally:
    db.close()
""",
        )
    )


def rollback_constraint_probes(project_id: str, media_id: str) -> dict:
    """Probe only this harness's rows; every attempted write is rolled back."""
    return json.loads(
        foundation.execute_python(
            "api",
            f"""
import json
from uuid import UUID,uuid4
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from app.core.config import Settings
from app.db.session import Database
db=Database(Settings())
project_id=UUID({project_id!r}); media_id=UUID({media_id!r})
result={{}}
def probe(name,query,parameters):
    with db.engine.connect() as connection:
        transaction=connection.begin()
        try:
            connection.execute(text(query),parameters)
        except SQLAlchemyError as error:
            result[name]={{'blocked':True,'sqlstate':getattr(error.orig,'sqlstate',None)}}
        else:
            result[name]={{'blocked':False,'sqlstate':None}}
        finally:
            transaction.rollback()
try:
    with db.engine.connect() as connection:
        first=dict(connection.execute(text('''
            SELECT id,revision_number,spec_json FROM edit_spec_revisions
            WHERE project_id=:project ORDER BY revision_number LIMIT 1
        '''),{{'project':project_id}}).mappings().one())
    probe('update_immutable','''
        UPDATE edit_spec_revisions SET spec_json=spec_json WHERE id=:id
    ''',{{'id':first['id']}})
    probe('delete_immutable','DELETE FROM edit_spec_revisions WHERE id=:id',{{'id':first['id']}})
    insert='''INSERT INTO edit_spec_revisions
        (id,project_id,revision_number,source_media_id,schema_version,spec_json,created_at)
        VALUES (:id,:project,:revision,:media,:schema,CAST(:spec AS JSONB),CURRENT_TIMESTAMP)'''
    base={{'id':uuid4(),'project':project_id,'revision':4,'media':media_id,
          'schema':1,'spec':json.dumps(first['spec_json'])}}
    for name,changes in (
        ('unique_revision',{{'revision':first['revision_number']}}),
        ('project_fk',{{'project':uuid4()}}),
        ('media_fk',{{'media':uuid4()}}),
        ('positive_revision',{{'revision':0}}),
        ('schema_unsupported',{{'schema':3}}),
    ):
        probe(name,insert,{{**base,**changes,'id':uuid4()}})
    print(json.dumps(result))
finally:
    db.close()
""",
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Gravar evidência JSON sem dados privados.")
    args = parser.parse_args()
    checks: list[dict] = []
    projects: list[str] = []
    cleanup: list[dict] = []
    failure = None
    outcome = "failed"
    retained_assets = []
    commands: list[dict] = []
    original_compose = foundation.compose

    def tracked_compose(*arguments: str, **kwargs) -> str:
        entry = {"command": "docker compose " + " ".join(arguments), "status": "running"}
        commands.append(entry)
        try:
            result = original_compose(*arguments, **kwargs)
        except Exception:
            entry["status"] = "failed"
            raise
        entry["status"] = "passed"
        return result

    foundation.compose = tracked_compose

    def require(value: object, message: str) -> None:
        foundation.require(bool(value), message)

    def passed(name: str, detail: object = None) -> None:
        checks.append({"check": name, "status": "passed", "detail": detail})
        print(json.dumps(checks[-1], ensure_ascii=False), flush=True)

    def error(status: int, body: object, expected: int, code: str | None = None) -> None:
        require(status == expected, f"Erro HTTP esperado {expected}, recebido {status}.")
        require(isinstance(body, dict), "Erro deve retornar objeto JSON.")
        require(set(body) == {"code", "message", "request_id"}, "Envelope de erro divergente.")
        require(body.get("request_id"), "Erro sem request_id.")
        if code:
            require(body.get("code") == code, f"Código de erro esperado: {code}.")
        require(
            not any(
                marker in json.dumps(body)
                for marker in ("Traceback", "postgresql", "/data/", "/home/", "SELECT ", "password")
            ),
            "Erro expõe detalhes privados de infraestrutura.",
        )

    def create(name: str) -> str:
        status, body = r02.json_request(
            "POST", "/api/v1/projects", {"name": name, "target_preset": "16:9"}
        )
        require(status == 201, "Projeto sintético não foi criado.")
        projects.append(body["id"])
        return body["id"]

    def put(path: str, spec: dict, expected: int) -> tuple[int, dict]:
        return r02.json_request("PUT", path, {**copy.deepcopy(spec), "expected_revision": expected})

    def validate_revision(body: dict, spec: dict, number: int, project: str) -> None:
        require(
            set(body)
            == {
                "id",
                "project_id",
                "revision_number",
                "created_at",
                "schema_version",
                "source_media_id",
                "trim",
                "canvas",
            },
            "Contrato público de revisão divergente.",
        )
        require(
            {name: body[name] for name in spec} == spec
            and body["revision_number"] == number
            and body["project_id"] == project,
            "Revisão retornada não corresponde ao plano enviado.",
        )
        require(body["id"] and body["created_at"], "Revisão sem identidade/data UTC.")

    try:
        foundation.wait_ready()
        revision = foundation.compose("exec", "-T", "api", "alembic", "current")
        require(
            any(
                head in revision
                for head in ("0003_r03_edit_spec_revisions", "0004_r04_text_audio_previews")
            ),
            "Migration R03 ou evolução R04 não está aplicada.",
        )
        foundation.compose("exec", "-T", "api", "alembic", "upgrade", "head")
        foundation.compose("exec", "-T", "api", "alembic", "check")
        passed("r03_migration_current_repeat_and_model_check")

        raw_fixtures = json.loads(foundation.execute_python("api", r02.FIXTURES))
        fixture = base64.b64decode(raw_fixtures["silent"])
        audio_fixture = base64.b64decode(raw_fixtures["audio"])
        token = uuid4().hex[:8]
        project = create(f"QA R03 revisions {token}")
        other = create(f"QA R03 foreign source {token}")
        status, media = r02.upload(project, fixture, "r03-revision-source.mp4")
        require(status == 201 and not media["has_audio"], "Importação MP4 sem áudio falhou.")
        status, foreign = r02.upload(other, audio_fixture, "r03-foreign-source.mp4")
        require(status == 201 and foreign["has_audio"], "Importação MP4 com áudio falhou.")
        retained_assets = [media["id"], foreign["id"]]
        baseline = r02.storage_snapshot(project)
        foreign_baseline = r02.storage_snapshot(other)
        require(
            media["checksum_sha256"] == hashlib.sha256(fixture).hexdigest()
            and media["byte_size"] == len(fixture)
            and baseline["assets"][0]["permissions"] == "0o400",
            "Integridade inicial do original inválida.",
        )
        passed(
            "real_r02_imports_audio_silent_sha256_and_readonly_originals",
            {
                "assets": list(retained_assets),
                "silent_bytes": len(fixture),
                "audio_bytes": len(audio_fixture),
            },
        )
        short_program = r02.FIXTURES.replace(
            "for name in ('audio','silent','audio_only'):", "for name in ('silent',):"
        ).replace("args+=['-t','1']", "args+=['-t','0.04']")
        short_fixture = base64.b64decode(
            json.loads(foundation.execute_python("api", short_program))["silent"]
        )
        short_project = create(f"QA R03 short source {token}")
        status, short_media = r02.upload(short_project, short_fixture, "r03-short.mp4")
        require(
            status == 201 and 0 < short_media["duration_ms"] < 100,
            "Amostra sintética menor que 100ms não foi importada.",
        )
        retained_assets.append(short_media["id"])
        short_path = f"/api/v1/projects/{short_project}/edit-spec"
        short_spec = {
            "schema_version": 1,
            "source_media_id": short_media["id"],
            "trim": {"start_ms": 0, "end_ms": short_media["duration_ms"]},
            "canvas": {"preset": "16:9", "fit": "contain", "focal_x": 0.5, "focal_y": 0.5},
        }
        status, short_saved = put(short_path, short_spec, 0)
        require(status == 201, "Fonte curta deveria aceitar o intervalo completo.")
        validate_revision(short_saved, short_spec, 1, short_project)
        for start, end in ((1, short_media["duration_ms"]), (0, short_media["duration_ms"] - 1)):
            invalid_short = {**short_spec, "trim": {"start_ms": start, "end_ms": end}}
            error(*put(short_path, invalid_short, 1), 422)
        short_baseline = r02.storage_snapshot(short_project)
        passed(
            "real_source_shorter_than_100ms_accepts_only_full_interval",
            {"duration_ms": short_media["duration_ms"]},
        )
        path = f"/api/v1/projects/{project}/edit-spec"
        error(*r02.request("GET", path), 404, "edit_spec_not_found")
        require(revision_snapshot(project) == [], "Projeto novo já contém revisões.")
        passed("unsaved_project_returns_edit_spec_not_found_404")

        spec = {
            "schema_version": 1,
            "source_media_id": media["id"],
            "trim": {"start_ms": 0, "end_ms": media["duration_ms"]},
            "canvas": {"preset": "9:16", "fit": "contain", "focal_x": 0.5, "focal_y": 0.5},
        }
        error(*put(path, spec, 1), 409, "edit_spec_conflict")
        require(revision_snapshot(project) == [], "Conflito inicial criou revisão.")
        status, first = put(path, spec, 0)
        require(status == 201, "Primeira revisão não retornou 201.")
        validate_revision(first, spec, 1, project)
        require(r02.request("GET", path) == (200, first), "GET não retornou a revisão inicial.")
        first_rows = revision_snapshot(project)
        require(len(first_rows) == 1, "Primeiro save criou quantidade incorreta de revisões.")
        passed("first_revision_expected_zero_201_and_get_latest")

        status, same = put(path, spec, 1)
        require(status == 200 and same == first, "Save idêntico não respeita política no-op.")
        require(revision_snapshot(project) == first_rows, "No-op alterou histórico.")
        error(*put(path, spec, 0), 409, "edit_spec_conflict")
        require(revision_snapshot(project) == first_rows, "Versão obsoleta alterou histórico.")
        passed("identical_current_save_is_noop_but_stale_revision_conflicts")

        status, patched = r02.json_request(
            "PATCH", f"/api/v1/projects/{project}", {"target_preset": "1:1"}
        )
        require(status == 200 and patched["target_preset"] == "1:1", "PATCH do preset falhou.")
        require(
            r02.request("GET", path) == (200, first), "Preset do cadastro alterou edição salva."
        )
        passed("project_target_preset_is_independent_from_saved_canvas")

        invalid = []
        for patch in (
            {"trim": {"start_ms": -1, "end_ms": 500}},
            {"trim": {"start_ms": 500, "end_ms": 500}},
            {"trim": {"start_ms": 0, "end_ms": media["duration_ms"] + 1}},
            {"trim": {"start_ms": 0, "end_ms": 99}},
            {"trim": {"start_ms": 0.0, "end_ms": 500}},
            {"trim": {"start_ms": True, "end_ms": 500}},
            {"trim": {"start_ms": "0", "end_ms": 500}},
            {"schema_version": 3},
            {"schema_version": True},
            {"schema_version": "1"},
            {"canvas": {**spec["canvas"], "preset": "2:3"}},
            {"canvas": {**spec["canvas"], "fit": "stretch"}},
            {"canvas": {**spec["canvas"], "focal_x": -0.01}},
            {"canvas": {**spec["canvas"], "focal_y": 1.01}},
            {"canvas": {**spec["canvas"], "focal_x": "0.5"}},
            {"canvas": {**spec["canvas"], "focal_y": True}},
            {"canvas": {**spec["canvas"], "private_path": "/private/secret"}},
            {"trim": {"start_ms": 0, "end_ms": 500, "extra": 1}},
            {"extra": "unsupported"},
        ):
            invalid.append({**copy.deepcopy(spec), **patch})
        for candidate in invalid:
            error(*put(path, candidate, 1), 422)
        for expected in (-1, 1.0, True, "1", None):
            error(*r02.json_request("PUT", path, {**spec, "expected_revision": expected}), 422)
        error(*r02.json_request("PUT", path, spec), 422)
        for nonfinite in (float("nan"), float("inf"), float("-inf")):
            candidate = copy.deepcopy(spec)
            candidate["canvas"]["focal_x"] = nonfinite
            error(*put(path, candidate, 1), 422)
        require(revision_snapshot(project) == first_rows, "Validação inválida alterou histórico.")
        passed("strict_validation_trim_schema_focal_types_extras_nonfinite_422", {"requests": 28})

        error(*put(path, {**spec, "source_media_id": foreign["id"]}, 1), 404)
        error(*put(path, {**spec, "source_media_id": str(uuid4())}, 1), 404)
        error(*r02.request("GET", f"/api/v1/projects/{uuid4()}/edit-spec"), 404)
        require(revision_snapshot(project) == first_rows, "Fonte cruzada alterou revisões.")
        passed("foreign_missing_source_and_missing_project_are_404")

        second_spec = copy.deepcopy(spec)
        second_spec["trim"] = {"start_ms": 100, "end_ms": 800}
        second_spec["canvas"] = {"preset": "4:5", "fit": "cover", "focal_x": 0.2, "focal_y": 0.8}
        status, second = put(path, second_spec, 1)
        require(status == 200, "Segunda revisão não retornou 200.")
        validate_revision(second, second_spec, 2, project)
        second_rows = revision_snapshot(project)
        require(
            len(second_rows) == 2 and second_rows[:1] == first_rows and second["id"] != first["id"],
            "Atualização sobrescreveu a revisão anterior.",
        )
        passed("second_revision_append_only_preserves_first")

        contenders = []
        for start, preset, focal in ((150, "1:1", 0.25), (200, "16:9", 0.75)):
            candidate = copy.deepcopy(second_spec)
            candidate["trim"] = {"start_ms": start, "end_ms": 850}
            candidate["canvas"] = {
                "preset": preset,
                "fit": "cover",
                "focal_x": focal,
                "focal_y": 0.5,
            }
            contenders.append(candidate)
        barrier = Barrier(3)

        def concurrent_save(candidate: dict) -> tuple[int, dict]:
            barrier.wait(timeout=10)
            return put(path, candidate, 2)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(concurrent_save, candidate) for candidate in contenders]
            barrier.wait(timeout=10)
            results = [future.result(timeout=35) for future in futures]
        require(
            sorted(status for status, _ in results) == [200, 409], "Corrida não produziu 200/409."
        )
        winner_index = next(index for index, result in enumerate(results) if result[0] == 200)
        winner = results[winner_index][1]
        validate_revision(winner, contenders[winner_index], 3, project)
        loser = next(result for result in results if result[0] == 409)
        error(*loser, 409, "edit_spec_conflict")
        rows = revision_snapshot(project)
        require(
            len(rows) == 3
            and rows[:2] == second_rows
            and [row["revision_number"] for row in rows] == [1, 2, 3],
            "Corrida duplicou ou sobrescreveu revisões.",
        )
        require(r02.request("GET", path) == (200, winner), "GET não retorna vencedor concorrente.")
        passed(
            "simultaneous_expected_two_updates_one_wins_one_409",
            {"statuses": sorted(r[0] for r in results)},
        )

        probes = rollback_constraint_probes(project, media["id"])
        require(
            all(item["blocked"] for item in probes.values()),
            "Trigger/constraint não bloqueou escrita.",
        )
        for name, code in (
            ("update_immutable", "55000"),
            ("delete_immutable", "55000"),
            ("unique_revision", "23505"),
            ("project_fk", "23503"),
            ("media_fk", "23503"),
            ("positive_revision", "23514"),
            ("schema_unsupported", "23514"),
        ):
            require(
                probes[name]["sqlstate"] == code, f"Constraint {name} retornou código inesperado."
            )
        require(revision_snapshot(project) == rows, "Probes SQL alteraram histórico.")
        passed("postgres_immutability_triggers_unique_foreign_keys_checks_with_rollback", probes)

        foundation.compose("restart", "db", "api", "worker")
        foundation.wait_ready()
        require(r02.request("GET", path) == (200, winner), "Edição não persistiu após restart.")
        require(revision_snapshot(project) == rows, "Histórico não persistiu após restart.")
        require(r02.storage_snapshot(project) == baseline, "Original mudou após edição/restart.")
        require(r02.storage_snapshot(other) == foreign_baseline, "Original de outro projeto mudou.")
        require(
            r02.request("GET", short_path) == (200, short_saved)
            and r02.storage_snapshot(short_project) == short_baseline,
            "Edição/original curto não persistiu após restart.",
        )
        passed("restart_preserves_latest_history_sha256_size_permissions_and_originals")

        require(
            r02.request("DELETE", f"/api/v1/projects/{project}")[0] == 204, "Soft delete falhou."
        )
        error(*r02.request("GET", path), 404)
        error(*put(path, contenders[0], 3), 404)
        error(*r02.request("GET", f"/api/v1/projects/{project}/media/{media['id']}/stream"), 404)
        require(revision_snapshot(project) == rows, "Soft delete removeu histórico.")
        require(r02.storage_snapshot(project) == baseline, "Soft delete alterou arquivo original.")
        passed("soft_deleted_project_cannot_get_save_or_stream_but_preserves_data")
        outcome = "passed"
    except Exception as caught:
        failure = (
            str(caught)
            if isinstance(caught, foundation.AcceptanceError)
            else f"Verificação interrompida ({type(caught).__name__})."
        )
    finally:
        try:
            foundation.compose("start", "db", "api", "worker")
            foundation.wait_ready()
        except Exception:
            outcome = "failed"
            failure = "Não foi possível restaurar disponibilidade dos serviços Edivo."
        for project_id in projects:
            try:
                status, _ = r02.request("DELETE", f"/api/v1/projects/{project_id}")
                cleanup.append({"project_id": project_id, "status": status})
                if status not in (204, 404):
                    outcome = "failed"
                    failure = "Não foi possível excluir logicamente projeto sintético de QA."
            except Exception:
                cleanup.append({"project_id": project_id, "status": "unavailable"})
                outcome = "failed"
                failure = "Limpeza lógica de QA indisponível; registros e arquivos preservados."
    report = {
        "status": outcome,
        "stage": "R03.2",
        "generated_at": datetime.now(UTC).isoformat(),
        "checks": checks,
        "completed_checks": len(checks),
        "failure": failure,
        "cleanup": cleanup,
        "retained_synthetic_asset_ids": retained_assets,
        "data_policy": (
            "Only QA projects soft-deleted; originals and revisions retained; "
            "SQL probes rolled back."
        ),
        "commands": commands,
    }
    foundation.compose = original_compose
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if outcome == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
