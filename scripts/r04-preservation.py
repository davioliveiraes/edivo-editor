"""Capture/verify the R04 baseline by reading historical files, rows and originals.

Never removes or changes application data. Run with no uploads in progress.
The capture destination is exclusive; verification writes a separate report.
"""

import argparse
import hashlib
import json
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/r04/evidence"
DATABASE_SNAPSHOT = """import hashlib,json
from sqlalchemy import text
from app.core.config import Settings
from app.db.session import Database
database=Database(Settings())
try:
    result={}
    with database.sessions() as session:
        for table in ('projects','media_assets','edit_spec_revisions'):
            query='SELECT id,row_to_json(t)::text FROM '+table+' t ORDER BY id'
            rows=session.execute(text(query))
            result[table]={str(row[0]):hashlib.sha256(row[1].encode()).hexdigest() for row in rows}
        result['alembic_revision']=session.scalar(text('SELECT version_num FROM alembic_version'))
    print(json.dumps(result))
finally:
    database.close()
"""


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def database_snapshot() -> dict:
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "api", "python", "-"],
        input=DATABASE_SNAPSHOT,
        text=True,
        capture_output=True,
        cwd=ROOT,
        timeout=60,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("database_snapshot_unavailable")
    return json.loads(result.stdout)


def historical_files() -> list[Path]:
    files = [
        ROOT / "docs/EDIVO_DOCUMENTACAO_PROJETO.md",
        ROOT / "docs/EDIVO_INSTRUCOES_CHATGPT.md",
        *(ROOT / "docs").glob("R0[0-4]_*.md"),
        *(ROOT / "backend/alembic/versions").glob("000[1-3]_*.py"),
        ROOT / "backend/uv.lock",
        ROOT / "frontend/package-lock.json",
    ]
    for release in ("r01", "r02", "r03"):
        files.extend(path for path in (ROOT / "docs" / release).rglob("*") if path.is_file())
    return sorted(set(files))


def original_snapshot() -> dict:
    files = {}
    for path in sorted((ROOT / "storage/originals").glob("*/*/source.mp4")):
        if any(item.is_symlink() for item in (path, *path.parents)):
            raise RuntimeError("unsafe_original")
        info = path.stat()
        files[path.parent.name] = {
            "project_id": path.parent.parent.name,
            "sha256": checksum(path),
            "bytes": info.st_size,
            "mode": f"{stat.S_IMODE(info.st_mode):04o}",
            "mtime_ns": info.st_mtime_ns,
        }
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", action="store_true")
    options = parser.parse_args()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    baseline_path = EVIDENCE / "baseline.json"
    try:
        if options.capture:
            value = {
                "recorded_at": datetime.now(UTC).isoformat(),
                "read_only_application_data": True,
                "historical_files": {
                    path.relative_to(ROOT).as_posix(): checksum(path) for path in historical_files()
                },
                "database": database_snapshot(),
                "originals": original_snapshot(),
            }
            with baseline_path.open("x") as output:
                json.dump(value, output, indent=2)
                output.write("\n")
            summary = {
                "status": "captured",
                "historical_files": len(value["historical_files"]),
                "originals": len(value["originals"]),
                "database_rows": {
                    table: len(rows)
                    for table, rows in value["database"].items()
                    if isinstance(rows, dict)
                },
                "alembic_revision": value["database"]["alembic_revision"],
            }
        else:
            baseline = json.loads(baseline_path.read_text())
            database, originals = database_snapshot(), original_snapshot()
            changed_files = [
                name
                for name, expected in baseline["historical_files"].items()
                if not (ROOT / name).is_file() or checksum(ROOT / name) != expected
            ]
            changed_rows = {
                table: [key for key, value in rows.items() if database[table].get(key) != value]
                for table, rows in baseline["database"].items()
                if isinstance(rows, dict)
            }
            changed_originals = [
                key for key, value in baseline["originals"].items() if originals.get(key) != value
            ]
            passed = not (changed_files or any(changed_rows.values()) or changed_originals)
            summary = {
                "status": "passed" if passed else "failed",
                "recorded_at": datetime.now(UTC).isoformat(),
                "read_only": True,
                "changed_historical_files": changed_files,
                "changed_database_rows": changed_rows,
                "changed_original_ids": changed_originals,
                "baseline_original_count": len(baseline["originals"]),
            }
            (EVIDENCE / "preservation.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary))
        return 0 if summary["status"] in {"captured", "passed"} else 1
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError):
        print(json.dumps({"status": "unavailable", "message": "Auditoria indisponível."}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
