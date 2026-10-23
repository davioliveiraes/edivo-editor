#!/usr/bin/env python3
"""Read-only reconciliation of originals, previews and final render artifacts.

Run during an idle maintenance window:
docker compose exec -T api python - < scripts/audit-storage-r05.py

Only stdout JSON is produced; no file, permission or database row is changed.
Unknown files/workspaces are evidence for review, never deletion candidates.
"""

import hashlib
import json
import os
import re
import stat
from collections import Counter
from contextlib import suppress
from datetime import UTC, datetime
from uuid import UUID

from app.core.config import Settings
from app.db.session import Database
from app.models.audio_asset import AudioAsset
from app.models.edit_spec_revision import EditSpecRevision
from app.models.media_asset import MediaAsset
from app.models.preview_job import PreviewJob
from app.models.render_job import RenderJob
from app.storage.local import CHUNK_BYTES, LocalStorage
from sqlalchemy import select, text

DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
CHECKSUM = re.compile(r"[a-f0-9]{64}", re.ASCII)


def identifier(value):
    """Never include an untrusted pathname component in the diagnostic JSON."""
    try:
        parsed = UUID(str(value))
        return str(parsed) if str(parsed) == str(value) else None
    except (ValueError, TypeError, AttributeError):
        return None


def open_root(path):
    """Pin the root and reject symlinks in every absolute ancestor."""
    if path.anchor != "/":
        raise OSError("unsafe_storage_root")
    descriptor = os.open("/", DIRECTORY_FLAGS)
    try:
        for component in path.parts[1:]:
            child = os.open(component, DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def scan_namespace(root_fd, namespace, filename, *, require_private):
    """Scan by pinned directory descriptors, without following unknown symlinks."""
    found, unsafe, directory_modes, empty_assets = set(), [], Counter(), []
    visited = set()

    def problem(code, components):
        unsafe.append(
            {
                "code": code,
                "project_id": identifier(components[0]) if components else None,
                "asset_id": identifier(components[1]) if len(components) > 1 else None,
            }
        )

    def walk(descriptor, components):
        metadata = os.fstat(descriptor)
        identity = metadata.st_dev, metadata.st_ino
        if identity in visited:
            problem("repeated_directory", components)
            return
        visited.add(identity)
        mode = stat.S_IMODE(metadata.st_mode)
        directory_modes[f"{mode:04o}"] += 1
        if require_private and mode != 0o700:
            problem("directory_mode_invalid", components)
        if require_private and metadata.st_uid != os.geteuid():
            problem("directory_owner_mismatch", components)
        with os.scandir(descriptor) as listing:
            entries = sorted(listing, key=lambda entry: entry.name)
        if len(components) == 2 and not entries:
            empty_assets.append(identifier(components[1]))
        for entry in entries:
            child_components = (*components, entry.name)
            try:
                metadata = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(metadata.st_mode):
                    problem("symlink_entry", child_components)
                elif stat.S_ISDIR(metadata.st_mode):
                    if len(child_components) > 2:
                        problem("unexpected_nested_directory", child_components)
                        continue
                    if identifier(entry.name) is None:
                        problem("invalid_identifier_directory", child_components)
                    child = os.open(entry.name, DIRECTORY_FLAGS, dir_fd=descriptor)
                    try:
                        walk(child, child_components)
                    finally:
                        os.close(child)
                elif not stat.S_ISREG(metadata.st_mode):
                    problem("nonregular_entry", child_components)
                elif (
                    len(components) == 2
                    and entry.name == filename
                    and all(identifier(value) for value in components)
                ):
                    found.add(f"{namespace}/{'/'.join(child_components)}")
                else:
                    problem("unexpected_file", child_components)
            except OSError:
                problem("entry_unavailable", child_components)

    try:
        descriptor = os.open(namespace, DIRECTORY_FLAGS, dir_fd=root_fd)
    except FileNotFoundError:
        return found, unsafe, directory_modes, empty_assets
    except OSError:
        problem("namespace_unavailable", ())
        return found, unsafe, directory_modes, empty_assets
    try:
        walk(descriptor, ())
    finally:
        os.close(descriptor)
    return found, unsafe, directory_modes, empty_assets


def verify_artifact(opener, row, key, size, expected):
    if (
        not isinstance(size, int)
        or size <= 0
        or not isinstance(expected, str)
        or CHECKSUM.fullmatch(expected) is None
    ):
        return "artifact_metadata_invalid", 0
    opened = None
    try:
        opened = opener(key, row.project_id, row.id, size)
        before = opened.stat_result
        if stat.S_IMODE(before.st_mode) != 0o400:
            return "artifact_mode_invalid", 0
        if before.st_uid != os.geteuid():
            return "artifact_owner_mismatch", 0
        count, digest = 0, hashlib.sha256()
        while chunk := opened.file.read(CHUNK_BYTES):
            count += len(chunk)
            if count > size:
                return "artifact_size_mismatch", 0
            digest.update(chunk)
        after = os.fstat(opened.file.fileno())
        fields = (
            "st_dev",
            "st_ino",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
            "st_mode",
        )
        if any(getattr(before, field) != getattr(after, field) for field in fields):
            return "artifact_changed_during_audit", 0
        if count != size:
            return "artifact_size_mismatch", 0
        if digest.hexdigest() != expected:
            return "artifact_checksum_mismatch", 0
        return None, count
    except Exception:  # noqa: BLE001 — safe code, never a filesystem path or raw exception
        return "artifact_unavailable", 0
    finally:
        if opened is not None:
            opened.file.close()


def temporary_inventory(root_fd, namespace):
    try:
        descriptor = os.open(namespace, DIRECTORY_FLAGS, dir_fd=root_fd)
    except FileNotFoundError:
        return {"entries": 0, "symlinks": 0, "status": "absent"}
    except OSError:
        return {"entries": None, "symlinks": None, "status": "unavailable"}
    try:
        with os.scandir(descriptor) as listing:
            entries = list(listing)
        symlinks = sum(
            stat.S_ISLNK(entry.stat(follow_symlinks=False).st_mode) for entry in entries
        )
        return {
            "entries": len(entries),
            "symlinks": symlinks,
            "directory_mode": f"{stat.S_IMODE(os.fstat(descriptor).st_mode):04o}",
            "status": "review_required" if entries else "empty",
        }
    finally:
        os.close(descriptor)


def main():
    database, root_fd = None, None
    try:
        settings = Settings()
        storage = LocalStorage(settings)
        root_fd = open_root(storage.root)
        root_stat = os.fstat(root_fd)
        root_private = stat.S_IMODE(root_stat.st_mode) == 0o700
        root_owned = root_stat.st_uid == os.geteuid()
        filesystem = os.fstatvfs(root_fd)
        unit = filesystem.f_frsize or filesystem.f_bsize
        available = filesystem.f_bavail * unit
        database = Database(settings)
        with database.sessions() as session:
            # PostgreSQL enforces read-only even if this diagnostic is changed accidentally.
            session.execute(text("SET TRANSACTION READ ONLY"))
            media = list(session.scalars(select(MediaAsset)))
            audio = list(session.scalars(select(AudioAsset)))
            previews = list(session.scalars(select(PreviewJob)))
            renders = list(session.scalars(select(RenderJob)))
            revision_projects = dict(
                session.execute(
                    select(EditSpecRevision.id, EditSpecRevision.project_id)
                ).all()
            )
        groups = [
            ("mp4", "originals", "source.mp4", media, storage.open_original),
            ("mp3", "originals-audio", "source.mp3", audio, storage.open_audio),
            ("preview", "previews", "preview.mp4", previews, storage.open_preview),
            ("render", "renders", "final.mp4", renders, storage.open_render),
        ]
        report = {
            "read_only": True,
            "observed_at_utc": datetime.now(UTC).isoformat(),
            "includes_soft_deleted_projects": True,
            "root_private": root_private,
            "root_owned_by_runtime": root_owned,
            "legacy_directory_policy": (
                "Existing original/preview directory modes are reported and preserved. "
                "Non0700 legacy ancestors require the historical baseline and private storage "
                "root; this script does not change modes or infer baseline preservation. "
                "New renders namespace/project/job directories require0700."
            ),
            "space": {
                "total_bytes": filesystem.f_blocks * unit,
                "used_bytes": (filesystem.f_blocks - filesystem.f_bfree) * unit,
                "free_bytes": filesystem.f_bfree * unit,
                "available_bytes": available,
                "minimum_reserve_bytes": settings.storage_min_free_bytes,
                "render_max_output_bytes": settings.render_max_output_bytes,
                "can_reserve_maximum_render": (
                    available
                    >= settings.storage_min_free_bytes
                    + settings.render_max_output_bytes
                ),
            },
            "job_states": {
                "preview": dict(Counter(row.status for row in previews)),
                "render": dict(Counter(row.status for row in renders)),
            },
            "inventories": {},
        }
        issues = (
            not root_private
            or not root_owned
            or available < settings.storage_min_free_bytes
        )
        for kind, namespace, filename, rows, opener in groups:
            known, invalid, verified_bytes, registered = set(), [], 0, 0
            for row in rows:
                if kind in {"preview", "render"}:
                    key, size, expected = (
                        row.artifact_storage_key,
                        row.artifact_byte_size,
                        row.artifact_sha256,
                    )
                    if (
                        revision_projects.get(row.edit_spec_revision_id)
                        != row.project_id
                    ):
                        invalid.append(
                            {"id": str(row.id), "code": "revision_project_mismatch"}
                        )
                    if key is None:
                        if row.status == "completed":
                            invalid.append(
                                {"id": str(row.id), "code": "artifact_metadata_invalid"}
                            )
                        continue
                    if kind == "render" and row.status != "completed":
                        invalid.append(
                            {"id": str(row.id), "code": "unpublished_artifact"}
                        )
                else:
                    key, size, expected = (
                        row.storage_key,
                        row.byte_size,
                        row.checksum_sha256,
                    )
                registered += 1
                if isinstance(key, str):
                    known.add(key)
                problem, byte_count = verify_artifact(opener, row, key, size, expected)
                verified_bytes += byte_count
                if problem:
                    invalid.append({"id": str(row.id), "code": problem})
            found, unsafe, modes, empty_assets = scan_namespace(
                root_fd, namespace, filename, require_private=kind == "render"
            )
            unregistered = [
                {"project_id": key.split("/")[1], "id": key.split("/")[2]}
                for key in sorted(found - known)
            ]
            report["inventories"][kind] = {
                "database_rows": len(rows),
                "registered": registered,
                "files_on_disk": len(found),
                "verified_bytes": verified_bytes,
                "invalid_ids": invalid,
                "unregistered_ids": unregistered,
                "unsafe_entries": unsafe,
                "directory_modes": dict(modes),
                "empty_asset_directory_ids": empty_assets,
            }
            issues |= bool(invalid or unregistered or unsafe or empty_assets)
        report["temporary"] = {
            namespace: temporary_inventory(root_fd, namespace)
            for namespace in ("temp", "workspaces")
        }
        report["temporary_entries"] = sum(
            inventory["entries"] or 0 for inventory in report["temporary"].values()
        )
        issues |= any(
            inventory["status"] not in {"absent", "empty"}
            for inventory in report["temporary"].values()
        )
        report["status"] = "review_required" if issues else "consistent"
        print(json.dumps(report, sort_keys=True))
        return int(issues)
    except Exception:  # noqa: BLE001 — never expose paths, credentials or stack traces
        print(
            json.dumps(
                {
                    "status": "unavailable",
                    "read_only": True,
                    "code": "audit_unavailable",
                }
            )
        )
        return 2
    finally:
        if root_fd is not None:
            with suppress(OSError):
                os.close(root_fd)
        if database is not None:
            with suppress(Exception):
                database.close()


if __name__ == "__main__":
    raise SystemExit(main())
