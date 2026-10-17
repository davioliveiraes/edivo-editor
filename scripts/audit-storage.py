#!/usr/bin/env python3
"""Audit storage against PostgreSQL, read-only; execute inside the API container.

Never removes uncertain originals or staging. Missing DB/storage aborts the audit
rather than reporting every original as an orphan. A quiet maintenance window
avoids mistaking an upload currently between promotion and commit for an orphan.
"""

import hashlib
import json
import re
import sys
from pathlib import Path

from app.core.config import Settings
from app.db.session import Database
from app.models.media_asset import MediaAsset
from sqlalchemy import select

KEY = re.compile(r"originals/[0-9a-f-]{36}/[0-9a-f-]{36}/source\.mp4")


def main() -> int:
    database = None
    try:
        settings = Settings()
        root = settings.storage_root
        if not root.is_dir() or any(p.is_symlink() for p in (root, *root.parents)):
            raise OSError("unsafe_storage")
        database = Database(settings)
        with database.sessions() as session:
            assets = list(session.scalars(select(MediaAsset)))
        known = set()
        missing, mismatches, unsafe = [], [], []
        for asset in assets:
            key = asset.storage_key
            if not KEY.fullmatch(key):
                unsafe.append(str(asset.id))
                continue
            path = root / key
            known.add(key)
            if any(p.is_symlink() for p in (path, *path.parents)):
                unsafe.append(str(asset.id))
                continue
            if not path.is_file():
                missing.append(str(asset.id))
                continue
            checksum = hashlib.sha256()
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    checksum.update(chunk)
            if (
                path.stat().st_size != asset.byte_size
                or checksum.hexdigest() != asset.checksum_sha256
            ):
                mismatches.append(str(asset.id))

        # Do not traverse symlink directories or disclose names from private paths.
        import os

        disk_keys = set()
        for directory, dirs, files in os.walk(root / "originals", followlinks=False):
            dirs[:] = [name for name in dirs if not (Path(directory) / name).is_symlink()]
            for name in files:
                path = Path(directory) / name
                key = path.relative_to(root).as_posix()
                if not path.is_symlink() and KEY.fullmatch(key):
                    disk_keys.add(key)
        orphan_ids = sorted(key.split("/")[2] for key in disk_keys - known)
        temp = root / "temp"
        temporary_count = (
            sum(1 for _ in temp.iterdir()) if temp.is_dir() and not temp.is_symlink() else 0
        )
        issues = bool(missing or mismatches or unsafe or orphan_ids or temporary_count)
        print(
            json.dumps(
                {
                    "status": "review_required" if issues else "consistent",
                    "read_only": True,
                    "registered_assets": len(assets),
                    "originals_on_disk": len(disk_keys),
                    "missing_asset_ids": missing,
                    "mismatched_asset_ids": mismatches,
                    "unsafe_asset_ids": unsafe,
                    "unregistered_original_ids": orphan_ids,
                    "temporary_entries": temporary_count,
                }
            )
        )
        return 1 if issues else 0
    except Exception:
        print(
            json.dumps(
                {
                    "status": "unavailable",
                    "read_only": True,
                    "message": "Auditoria indisponível; nenhum arquivo foi alterado.",
                }
            )
        )
        return 2
    finally:
        if database is not None:
            database.close()


if __name__ == "__main__":
    sys.exit(main())
