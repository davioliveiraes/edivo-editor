#!/usr/bin/env python3
"""VIS-01 read-only before/after preservation. Never changes app data/lifecycle.

Run from repository root, outside a sandbox that cannot access Docker:
  python3 docs/design-refresh/preservation.py --stage before --output docs/design-refresh/evidence/preservation-before.json
  python3 docs/design-refresh/preservation.py --stage after --before docs/design-refresh/evidence/preservation-before.json --output docs/design-refresh/evidence/preservation-after.json
Only a NEW output JSON below docs/design-refresh/evidence is created. Backend,
SQL rows, triggers, original media, artifacts, Compose and other containers are
read only. Private paths, row contents, credentials and DSNs are never printed.
"""

import argparse
import hashlib
import json
import os
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs/design-refresh/evidence"
TABLES = (
    "projects",
    "media_assets",
    "audio_assets",
    "edit_spec_revisions",
    "preview_jobs",
    "render_jobs",
)
APPLICATION = r"""
import hashlib,json,os,stat
from sqlalchemy import text
from app.core.config import Settings
from app.db.session import Database
from app.storage.local import LocalStorage
settings=Settings(); database=Database(settings); storage=LocalStorage(settings)
result={'rows':{},'artifacts':{},'artifact_errors':{},'trigger_hashes':{}}
try:
 with database.sessions() as session, session.begin():
  session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
  for table in TABLES:
   result['rows'][table]={str(row[0]):hashlib.sha256(row[1].encode()).hexdigest() for row in session.execute(text('SELECT id,row_to_json(t)::text FROM '+table+' t ORDER BY id'))}
  result['alembic_revision']=session.scalar(text('SELECT version_num FROM alembic_version'))
  result['trigger_hashes']={row[0]:hashlib.sha256(row[1].encode()).hexdigest() for row in session.execute(text("SELECT tgname,pg_get_triggerdef(oid) FROM pg_trigger WHERE tgrelid='edit_spec_revisions'::regclass AND NOT tgisinternal ORDER BY tgname"))}
  groups={
   'mp4':('media_assets','storage_key','byte_size','checksum_sha256',storage.open_original),
   'mp3':('audio_assets','storage_key','byte_size','checksum_sha256',storage.open_audio),
   'preview':('preview_jobs','artifact_storage_key','artifact_byte_size','artifact_sha256',storage.open_preview),
   'render':('render_jobs','artifact_storage_key','artifact_byte_size','artifact_sha256',storage.open_render)}
  for kind,(table,key_column,size_column,sha_column,opener) in groups.items():
   found={}; errors=[]
   query='SELECT id,project_id,'+key_column+','+size_column+','+sha_column+' FROM '+table+' WHERE '+key_column+' IS NOT NULL ORDER BY id'
   for row in session.execute(text(query)):
    try:
     opened=opener(row[2],row[1],row[0],row[3])
     with opened.file as stream:
      before=os.fstat(stream.fileno()); digest=hashlib.sha256()
      while chunk:=stream.read(1048576): digest.update(chunk)
      after=os.fstat(stream.fileno())
      assert (before.st_size,before.st_mode,before.st_mtime_ns,before.st_ino)==(after.st_size,after.st_mode,after.st_mtime_ns,after.st_ino)
      assert digest.hexdigest()==row[4] and stat.S_IMODE(before.st_mode)==0o400
      found[str(row[0])]={'project_id':str(row[1]),'sha256':digest.hexdigest(),'byte_size':before.st_size,'mode':f'{stat.S_IMODE(before.st_mode):04o}','mtime_ns':before.st_mtime_ns,'uid':before.st_uid,'gid':before.st_gid}
    except Exception: errors.append(str(row[0]))
   result['artifacts'][kind]=found;result['artifact_errors'][kind]=errors
  result['active_queues']={table:session.scalar(text('SELECT count(*) FROM '+table+" WHERE status IN ('queued','running')")) for table in ('preview_jobs','render_jobs')}
 print(json.dumps(result))
finally: database.close()
"""
INSPECT_TEMPLATE = r'{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},"state":{{json .State.Status}},"started_at":{{json .State.StartedAt}},"finished_at":{{json .State.FinishedAt}},"ports":{{json .NetworkSettings.Ports}},"mounts":{{json .Mounts}},"user":{{json .Config.User}},"project":{{json (index .Config.Labels "com.docker.compose.project")}},"service":{{json (index .Config.Labels "com.docker.compose.service")}}}'


def execute(argv, *, input_text=None, accepted_exit_codes=(0,)):
    completed = subprocess.run(
        argv,
        cwd=ROOT,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    if completed.returncode not in accepted_exit_codes:
        raise RuntimeError("read_only_command_unavailable")
    return completed.stdout


def checksum(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as source:
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("not_regular_file")
        digest = hashlib.sha256()
        while chunk := source.read(1048576):
            digest.update(chunk)
    return digest.hexdigest()


def filesystem_snapshot(baseline):
    result = {}
    for name in baseline["protected_files"]:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("invalid_baseline_entry")
        try:
            result[name] = checksum(ROOT / relative)
        except OSError:
            result[name] = None
    private = ROOT / ".env"
    private_metadata = None
    if private.exists():
        private_metadata = {
            "sha256": checksum(private),
            "mode": f"{stat.S_IMODE(private.stat().st_mode):04o}",
        }
    return {
        "protected_files": result,
        "private_environment_hash_mode": private_metadata,
        "git_head": execute(["git", "rev-parse", "HEAD"]).strip(),
    }


def containers_snapshot():
    listed = [
        json.loads(line)
        for line in execute(
            ["docker", "ps", "-a", "--format", "{{json .}}"]
        ).splitlines()
        if line.strip()
    ]
    relevant = [
        item
        for item in listed
        if "obra" in item.get("Names", "").casefold()
        or item.get("Names", "").startswith("edivo-")
    ]
    result = {"obra": {}, "edivo_protected": {}, "frontend_allowed_deployment": {}}
    for item in relevant:
        record = json.loads(
            execute(["docker", "inspect", "--format", INSPECT_TEMPLATE, item["ID"]])
        )
        # Preserve mount identity by hash without copying host/private paths.
        record["mounts"] = [
            {
                "type": mount.get("Type"),
                "destination": mount.get("Destination"),
                "rw": mount.get("RW"),
                "driver": mount.get("Driver"),
                "source_sha256": hashlib.sha256(
                    str(mount.get("Source", "")).encode()
                ).hexdigest(),
            }
            for mount in record["mounts"]
        ]
        if "obra" in record["name"].casefold():
            result["obra"][record["id"]] = record
        elif record["project"] == "edivo" and record["service"] in {
            "api",
            "db",
            "worker",
            "migrate",
        }:
            result["edivo_protected"][record["service"]] = record
        elif record["project"] == "edivo" and record["service"] == "frontend":
            result["frontend_allowed_deployment"] = record
    return result


def differences(before, after):
    return {
        "added": sorted(after.keys() - before.keys()),
        "removed": sorted(before.keys() - after.keys()),
        "changed": sorted(
            key for key in before.keys() & after.keys() if before[key] != after[key]
        ),
    }


def any_difference(value):
    return any(value[key] for key in ("added", "removed", "changed"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("before", "after"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--before", type=Path)
    parser.add_argument("--allow-file", action="append", default=[])
    args = parser.parse_args()
    output = args.output.resolve()
    if output.parent != EVIDENCE.resolve() or output.exists():
        parser.error("Use a NEW JSON directly in docs/design-refresh/evidence.")
    if args.stage == "after" and args.before is None:
        parser.error("--before is required for after.")
    allowed = set(args.allow_file)
    if not allowed <= {"DESIGN.md"}:
        parser.error("Only a justified DESIGN.md adjustment may be authorized here.")
    report = {
        "stage": args.stage,
        "status": "unavailable",
        "observed_at": datetime.now(UTC).isoformat(),
        "read_only_application_data": True,
        "lifecycle_performed": False,
    }
    try:
        baseline = json.loads((EVIDENCE / "baseline.json").read_text())
        report["filesystem"] = filesystem_snapshot(baseline)
        application_code = "TABLES=" + repr(TABLES) + "\n" + APPLICATION
        report["application"] = json.loads(
            execute(
                ["docker", "compose", "exec", "-T", "api", "python", "-"],
                input_text=application_code,
            )
        )
        audit = json.loads(
            execute(
                ["docker", "compose", "exec", "-T", "api", "python", "-"],
                input_text=(ROOT / "scripts/audit-storage-r05.py").read_text(),
                accepted_exit_codes=(0, 1, 2),
            )
        )
        report["storage_audit"] = audit
        report["containers"] = containers_snapshot()
        changed_from_original = [
            name
            for name, sha in baseline["protected_files"].items()
            if report["filesystem"]["protected_files"].get(name) != sha
        ]
        report["protected_changed_from_visual_baseline"] = [
            name for name in changed_from_original if name not in allowed
        ]
        report["authorized_document_changes"] = [
            name for name in changed_from_original if name in allowed
        ]
        issues = bool(
            report["protected_changed_from_visual_baseline"]
            or any(report["application"]["artifact_errors"].values())
            or audit["status"] != "consistent"
        )
        if args.stage == "after":
            before = json.loads(args.before.read_text())
            comparison = {"rows": {}, "artifacts": {}}
            for table in TABLES:
                diff = differences(
                    before["application"]["rows"][table],
                    report["application"]["rows"][table],
                )
                comparison["rows"][table] = diff
                issues |= any_difference(diff)
            for kind in ("mp4", "mp3", "preview", "render"):
                diff = differences(
                    before["application"]["artifacts"][kind],
                    report["application"]["artifacts"][kind],
                )
                comparison["artifacts"][kind] = diff
                issues |= any_difference(diff)
            comparison["trigger_unchanged"] = (
                before["application"]["trigger_hashes"]
                == report["application"]["trigger_hashes"]
            )
            comparison["alembic_unchanged"] = (
                before["application"]["alembic_revision"]
                == report["application"]["alembic_revision"]
            )
            comparison["env_unchanged"] = (
                before["filesystem"]["private_environment_hash_mode"]
                == report["filesystem"]["private_environment_hash_mode"]
            )
            comparison["git_head_unchanged"] = (
                before["filesystem"]["git_head"] == report["filesystem"]["git_head"]
            )
            comparison["obra_containers_unchanged"] = (
                before["containers"]["obra"] == report["containers"]["obra"]
            )
            comparison["backend_containers_unchanged"] = (
                before["containers"]["edivo_protected"]
                == report["containers"]["edivo_protected"]
            )
            comparison["frontend_ports_unchanged"] = before["containers"][
                "frontend_allowed_deployment"
            ].get("ports") == report["containers"]["frontend_allowed_deployment"].get(
                "ports"
            )
            issues |= not all(
                value for key, value in comparison.items() if isinstance(value, bool)
            )
            report["comparison"] = comparison
        report["status"] = "review_required" if issues else "passed"
    except Exception as error:  # noqa: BLE001 — sanitized QA boundary
        # Diagnostic codes only: no stderr, command text, credentials or paths.
        report["error_code"] = "read_only_snapshot_unavailable"
        report["error_type"] = type(error).__name__
    with output.open("x") as destination:
        json.dump(report, destination, indent=2)
        destination.write("\n")
    print(
        json.dumps(
            {
                "stage": args.stage,
                "status": report["status"],
                "report": str(output.relative_to(ROOT)),
                "rows": {
                    table: len(rows)
                    for table, rows in report.get("application", {})
                    .get("rows", {})
                    .items()
                },
                "artifacts": {
                    kind: len(rows)
                    for kind, rows in report.get("application", {})
                    .get("artifacts", {})
                    .items()
                },
            }
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
