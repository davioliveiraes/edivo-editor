#!/usr/bin/env python3
"""Verify the frozen R05 baseline without changing application data or services.

Run during an idle QA window. A NEW --output JSON below docs/r05/evidence is
required; existing evidence is never overwritten. New application rows/files
are allowed, while every baseline row, original and preview must stay intact.
This does not replace the separate read-only audit of new/unknown artifacts.
"""

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/r05/evidence"
BASELINE = EVIDENCE / "baseline.json"
TABLES = (
    "projects",
    "media_assets",
    "audio_assets",
    "edit_spec_revisions",
    "preview_jobs",
)
APPLICATION = """
import hashlib,json,os,stat
from sqlalchemy import text
from app.core.config import Settings
from app.db.session import Database
from app.storage.local import LocalStorage
settings=Settings()
database=Database(settings)
storage=LocalStorage(settings)
result={'rows':{},'artifacts':{},'artifact_errors':{},'revision_trigger_hashes':{}}
try:
    with database.sessions() as session, session.begin():
        session.execute(text('SET TRANSACTION READ ONLY'))
        for table in TABLES:
            query='SELECT id,row_to_json(t)::text FROM '+table+' t ORDER BY id'
            result['rows'][table]={str(row[0]):hashlib.sha256(row[1].encode()).hexdigest()
                                  for row in session.execute(text(query))}
        result['alembic_revision']=session.scalar(text('SELECT version_num FROM alembic_version'))
        trigger_query=("SELECT tgname,pg_get_triggerdef(oid) FROM pg_trigger "
            "WHERE tgrelid='edit_spec_revisions'::regclass AND NOT tgisinternal ORDER BY tgname")
        result['revision_trigger_hashes']={row[0]:hashlib.sha256(row[1].encode()).hexdigest()
                                          for row in session.execute(text(trigger_query))}
        groups={'mp4':('media_assets','storage_key','byte_size',storage.open_original),
                'mp3':('audio_assets','storage_key','byte_size',storage.open_audio),
                'preview':('preview_jobs','artifact_storage_key','artifact_byte_size',storage.open_preview)}
        for kind,expected_files in EXPECTED_ARTIFACTS.items():
            table,key_column,size_column,opener=groups[kind]
            actual,errors={},[]
            for asset_id,expected in expected_files.items():
                query=('SELECT id,project_id,'+key_column+','+size_column+
                       ' FROM '+table+' WHERE id=:id')
                row=session.execute(text(query),{'id':asset_id}).first()
                try:
                    if row is None or str(row[1])!=expected['project_id']:
                        raise ValueError('baseline_artifact_mismatch')
                    opened=opener(row[2],row[1],row[0],row[3])
                    with opened.file as stream:
                        before=os.fstat(stream.fileno())
                        digest=hashlib.sha256()
                        while chunk:=stream.read(1048576):
                            digest.update(chunk)
                        after=os.fstat(stream.fileno())
                        if (before.st_size,before.st_mode,before.st_mtime_ns,before.st_ino)!=(
                            after.st_size,after.st_mode,after.st_mtime_ns,after.st_ino):
                            raise ValueError('artifact_changed_during_read')
                        actual[asset_id]={'project_id':str(row[1]),'sha256':digest.hexdigest(),
                            'byte_size':before.st_size,'mode':f'{stat.S_IMODE(before.st_mode):04o}',
                            'mtime_ns':before.st_mtime_ns,'uid':before.st_uid,'gid':before.st_gid}
                except Exception:
                    errors.append(asset_id)
            result['artifacts'][kind]=actual
            result['artifact_errors'][kind]=errors
    print(json.dumps(result))
finally:
    database.close()
"""


def execute(argv: list[str], *, input_text: str | None = None) -> str:
    result = subprocess.run(
        argv,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
        cwd=ROOT,
        timeout=180,
    )
    if result.returncode:
        # Never expose raw Docker/SQL stderr, argv, environment or a DSN.
        raise RuntimeError("read_only_snapshot_unavailable")
    return result.stdout


def checksum(path: Path) -> str:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise OSError("historical_file_not_regular")
        digest = hashlib.sha256()
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def historical_changes(expected: dict) -> list[str]:
    changed = []
    for name, sha256 in expected.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("invalid_historical_entry")
        path = ROOT / relative
        try:
            if (
                any(parent.is_symlink() for parent in path.parents)
                or checksum(path) != sha256
            ):
                changed.append(name)
        except OSError:
            changed.append(name)
    return changed


def application_snapshot(expected: dict) -> dict:
    preamble = (
        "TABLES="
        + repr(TABLES)
        + "\nEXPECTED_ARTIFACTS="
        + repr(expected["artifacts"])
        + "\n"
    )
    return json.loads(
        execute(
            ["docker", "compose", "exec", "-T", "api", "python", "-"],
            input_text=preamble + APPLICATION,
        )
    )


def obra_snapshot(expected: list[dict]) -> list[dict]:
    if not expected:
        return []
    identifiers = [item["id"] for item in expected]
    if any(
        re.fullmatch(r"[a-f0-9]{64}", identifier) is None for identifier in identifiers
    ):
        raise ValueError("invalid_container_baseline")
    # Docker renders only these public metadata fields; Config.Env is never read.
    template = (
        '{"id":{{json .Id}},"name":{{json .Name}},"state":{{json .State.Status}},'
        '"image":{{json .Config.Image}},"ports":{{json .HostConfig.PortBindings}}}'
    )
    return [
        json.loads(line)
        for line in execute(
            ["docker", "inspect", "--format", template, *identifiers]
        ).splitlines()
        if line.strip()
    ]


def verify(baseline: dict) -> dict:
    application = application_snapshot(baseline["application"])
    changed_rows = {
        table: [
            identifier
            for identifier, expected in rows.items()
            if application["rows"][table].get(identifier) != expected
        ]
        for table, rows in baseline["application"]["rows"].items()
    }
    changed_artifacts = {
        kind: [
            identifier
            for identifier, expected in artifacts.items()
            if application["artifacts"][kind].get(identifier) != expected
            or expected["mode"] != "0400"
        ]
        for kind, artifacts in baseline["application"]["artifacts"].items()
    }
    current_containers = {
        item["id"]: item for item in obra_snapshot(baseline["obra_control"])
    }
    changed_containers = [
        item["name"]
        for item in baseline["obra_control"]
        if current_containers.get(item["id"]) != item
    ]
    changed_files = historical_changes(baseline["historical_files"])
    trigger_unchanged = (
        application["revision_trigger_hashes"]
        == baseline["application"]["revision_trigger_hashes"]
    )
    git_head = execute(["git", "rev-parse", "HEAD"]).strip()
    git_unchanged = git_head == baseline["git_head"]
    env_mode = f"{stat.S_IMODE((ROOT / '.env').stat().st_mode):04o}"
    env_mode_unchanged = env_mode == baseline["env_mode"]
    passed = not (
        changed_files
        or any(changed_rows.values())
        or any(changed_artifacts.values())
        or changed_containers
        or not trigger_unchanged
        or not git_unchanged
        or not env_mode_unchanged
    )
    return {
        "status": "passed" if passed else "failed",
        "recorded_at": datetime.now(UTC).isoformat(),
        "baseline_recorded_at": baseline["recorded_at"],
        "read_only_application_data": True,
        "additions_allowed": True,
        "historical_file_count": len(baseline["historical_files"]),
        "changed_historical_files": changed_files,
        "baseline_database_rows": {
            table: len(rows) for table, rows in baseline["application"]["rows"].items()
        },
        "current_database_rows": {
            table: len(rows) for table, rows in application["rows"].items()
        },
        "changed_database_rows": changed_rows,
        "baseline_artifacts": {
            kind: len(rows)
            for kind, rows in baseline["application"]["artifacts"].items()
        },
        "changed_artifact_ids": changed_artifacts,
        "unreadable_artifact_ids": application["artifact_errors"],
        "revision_trigger_unchanged": trigger_unchanged,
        "alembic_baseline": baseline["application"]["alembic_revision"],
        "alembic_current": application["alembic_revision"],
        "obra_container_count": len(baseline["obra_control"]),
        "changed_obra_containers": changed_containers,
        "git_head_unchanged": git_unchanged,
        "git_head": git_head,
        "env_mode_unchanged": env_mode_unchanged,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, required=True, help="Novo JSON em docs/r05/evidence."
    )
    options = parser.parse_args()
    output = options.output if options.output.is_absolute() else ROOT / options.output
    output = output.absolute()
    if (
        output.suffix != ".json"
        or output.parent.resolve() != EVIDENCE.resolve()
        or output.exists()
    ):
        parser.error("--output deve ser um JSON novo diretamente em docs/r05/evidence.")
    try:
        baseline = json.loads(BASELINE.read_text())
        report = verify(baseline)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        RuntimeError,
        subprocess.SubprocessError,
    ):
        report = {
            "status": "unavailable",
            "recorded_at": datetime.now(UTC).isoformat(),
            "read_only_application_data": True,
            "error_code": "preservation_snapshot_unavailable",
        }
    try:
        descriptor = os.open(
            output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "w") as target:
            json.dump(report, target, indent=2, ensure_ascii=False)
            target.write("\n")
    except OSError:
        print(
            json.dumps(
                {"status": "unavailable", "error_code": "evidence_output_unavailable"}
            )
        )
        return 2
    print(json.dumps(report, ensure_ascii=False))
    return (
        0
        if report["status"] == "passed"
        else 2
        if report["status"] == "unavailable"
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
