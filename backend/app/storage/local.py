"""Private staging and no-replace publication of immutable source files."""

import hashlib
import logging
import os
import re
import shutil
import stat
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

from app.core.config import Settings
from app.core.errors import AppError

logger = logging.getLogger("edivo.storage")
CHUNK_BYTES = 1024 * 1024


def sanitize_filename(filename: str | None) -> str:
    # Names are display-only. Both Windows and POSIX directory separators are stripped.
    name = unicodedata.normalize("NFKC", filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if not unicodedata.category(c).startswith("C"))
    name = re.sub(r'[<>:"|?*]', "_", name).strip(" .")
    stem, suffix = os.path.splitext(name)
    return f"{stem[:156]}{suffix[:4]}" if stem else "video.mp4"


@dataclass(frozen=True)
class StagedFile:
    path: Path
    byte_size: int
    checksum_sha256: str


class LocalStorage:
    def __init__(self, settings: Settings) -> None:
        self.root = settings.storage_root.absolute()
        self.settings = settings

    def _directory(self, directory: Path) -> Path:
        # Existing symlinks are rejected rather than followed, including root ancestors.
        for part in (directory, *directory.parents):
            if part.is_symlink():
                raise OSError("unsafe_storage_directory")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not directory.is_dir():
            raise OSError("invalid_storage_directory")
        return directory

    def ensure_space(self, additional_bytes: int = 0) -> None:
        if (
            shutil.disk_usage(self.root).free
            < self.settings.storage_min_free_bytes + additional_bytes
        ):
            raise OSError("insufficient_storage_space")

    def prepare_temp(self) -> Path:
        try:
            # A missing storage mount must not silently create an alternate storage root.
            if not self.root.is_dir():
                raise OSError("storage_missing")
            directory = self._directory(self.root / "temp")
            self.ensure_space()
            return directory
        except OSError:
            raise AppError(
                503, "storage_unavailable", "Armazenamento temporariamente indisponível."
            ) from None

    def stage(self, source: BinaryIO) -> StagedFile:
        path: Path | None = None
        try:
            directory = self.prepare_temp()
            fd, name = tempfile.mkstemp(prefix="upload-", suffix=".mp4", dir=directory)
            path = Path(name)
            byte_size = 0
            checksum = hashlib.sha256()
            with os.fdopen(fd, "wb") as target:
                while chunk := source.read(CHUNK_BYTES):
                    byte_size += len(chunk)
                    if byte_size > self.settings.upload_max_bytes:
                        raise AppError(
                            413, "upload_too_large", "O arquivo excede o limite permitido."
                        )
                    self.ensure_space(len(chunk))
                    target.write(chunk)
                    checksum.update(chunk)
                target.flush()
                os.fsync(target.fileno())
            if byte_size == 0:
                raise AppError(422, "invalid_media", "O arquivo está vazio.")
            return StagedFile(path, byte_size, checksum.hexdigest())
        except BaseException as error:
            if path is not None:
                self.discard_staging(path)
            if isinstance(error, OSError):
                raise AppError(
                    503, "storage_unavailable", "Armazenamento temporariamente indisponível."
                ) from None
            raise

    def promote(self, staged: StagedFile, project_id: UUID, asset_id: UUID) -> str:
        destination: Path | None = None
        created_directory = False
        try:
            parent = self._directory(self.root / "originals" / str(project_id))
            destination = parent / str(asset_id)
            # Atomic exclusive mkdir prevents accidentally adopting another asset directory.
            destination.mkdir(mode=0o700, exist_ok=False)
            created_directory = True
            target = destination / "source.mp4"
            os.chmod(staged.path, stat.S_IRUSR)
            # link() is atomic and fails with EEXIST; never rename/replace over an original.
            os.link(staged.path, target, follow_symlinks=False)
            directory_fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            self.discard_staging(staged.path)
            return target.relative_to(self.root).as_posix()
        except OSError:
            if created_directory and destination is not None:
                self.discard_original(destination / "source.mp4")
            raise AppError(
                503, "storage_unavailable", "Não foi possível preservar o arquivo original."
            ) from None

    @staticmethod
    def discard_staging(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.error("upload_staging_cleanup_failed")

    @staticmethod
    def discard_original(path: Path) -> None:
        """Compensate only a file created by the current upload, never pre-existing originals."""
        try:
            path.unlink(missing_ok=True)
            path.parent.rmdir()
        except OSError:
            logger.error("upload_compensation_failed")
