#!/usr/bin/env python3
"""Read-only reconciliation of MP4, MP3 and canonical R04 preview files.

Execute via docker compose exec -T api python - < scripts/audit-storage-r04.py.
Unknown/uncertain artifacts and temporary entries are reported, never removed.
Use an idle maintenance window to avoid observing in-flight publication.
"""

import hashlib
import json
import os
import stat

from sqlalchemy import select

from app.core.config import Settings
from app.db.session import Database
from app.models.audio_asset import AudioAsset
from app.models.media_asset import MediaAsset
from app.models.preview_job import PreviewJob
from app.storage.local import LocalStorage


def main():
    database = None
    try:
        settings = Settings()
        storage = LocalStorage(settings)
        database = Database(settings)
        if not storage.root.is_dir() or any(
            p.is_symlink() for p in (storage.root, *storage.root.parents)
        ):
            raise OSError("unsafe_storage_root")
        with database.sessions() as session:
            groups = [
                (
                    "mp4",
                    "originals",
                    "source.mp4",
                    list(session.scalars(select(MediaAsset))),
                    storage.open_original,
                ),
                (
                    "mp3",
                    "originals-audio",
                    "source.mp3",
                    list(session.scalars(select(AudioAsset))),
                    storage.open_audio,
                ),
                (
                    "preview",
                    "previews",
                    "preview.mp4",
                    list(session.scalars(select(PreviewJob))),
                    storage.open_preview,
                ),
            ]
        report = {"read_only": True, "inventories": {}, "temporary_entries": 0}
        issues = False
        for kind, namespace, filename, rows, opener in groups:
            known, bad = set(), []
            registered = 0
            for row in rows:
                if kind == "preview":
                    if row.artifact_storage_key is None:
                        continue
                    key, size, expected = (
                        row.artifact_storage_key,
                        row.artifact_byte_size,
                        row.artifact_sha256,
                    )
                else:
                    key, size, expected = (
                        row.storage_key,
                        row.byte_size,
                        row.checksum_sha256,
                    )
                registered += 1
                known.add(key)
                opened = None
                try:
                    opened = opener(key, row.project_id, row.id, size)
                    digest = hashlib.sha256()
                    with opened.file as stream:
                        while chunk := stream.read(1048576):
                            digest.update(chunk)
                    metadata = opened.stat_result
                    if stat.S_IMODE(metadata.st_mode) != 0o400 or digest.hexdigest() != expected:
                        bad.append(str(row.id))
                except Exception:  # noqa: BLE001 — record safe IDs without raw FS/SQL exceptions
                    if opened is not None:
                        opened.file.close()
                    bad.append(str(row.id))
            found, unsafe = set(), []
            for directory, dirs, files in os.walk(storage.root / namespace, followlinks=False):
                for name in list(dirs):
                    path = storage.root / directory / name
                    if path.is_symlink():
                        unsafe.append("symlink_directory")
                        dirs.remove(name)
                for name in files:
                    path = storage.root / directory / name
                    if path.is_symlink():
                        unsafe.append("symlink_file")
                    elif name == filename:
                        found.add(path.relative_to(storage.root).as_posix())
                    else:
                        unsafe.append("unexpected_file")
            unknown = sorted(
                key.split("/")[2] if len(key.split("/")) == 4 else "invalid_key"
                for key in found - known
            )
            report["inventories"][kind] = {
                "registered": registered,
                "files_on_disk": len(found),
                "invalid_ids": bad,
                "unregistered_ids": unknown,
                "unsafe_entries": unsafe,
            }
            issues |= bool(bad or unknown or unsafe)
        temporary = storage.root / "temp"
        if temporary.is_symlink():
            raise OSError("unsafe_temp")
        report["temporary_entries"] = (
            sum(1 for _ in temporary.iterdir()) if temporary.is_dir() else 0
        )
        issues |= report["temporary_entries"] > 0
        report["status"] = "review_required" if issues else "consistent"
        print(json.dumps(report))
        return int(issues)
    except Exception:  # noqa: BLE001 — do not expose credentials, paths or tracebacks
        print(json.dumps({"status": "unavailable", "read_only": True}))
        return 2
    finally:
        if database is not None:
            database.close()


if __name__ == "__main__":
    raise SystemExit(main())
