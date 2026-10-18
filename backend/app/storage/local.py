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


@dataclass(frozen=True)
class OpenedOriginal:
    """A validated, pinned inode; callers own and must close the descriptor."""

    file: BinaryIO
    stat_result: os.stat_result


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

    def open_original(
        self, storage_key: str, project_id: UUID, asset_id: UUID, byte_size: int
    ) -> OpenedOriginal:
        """Open only the authorized original, without following any path symlink.

        Directory-relative opens pin every parent inode, including root ancestors.
        Subsequent reads use the returned descriptor, never a second pathname open.
        O_NONBLOCK also prevents an unexpected FIFO from blocking before fstat.
        """
        directory_fd = None
        file_fd = None
        try:
            expected_key = f"originals/{project_id}/{asset_id}/source.mp4"
            if storage_key != expected_key or byte_size <= 0:
                raise OSError("invalid_original_key")
            root_parts = self.root.parts
            if self.root.anchor != "/":
                raise OSError("invalid_storage_root")
            directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            directory_fd = os.open("/", directory_flags)
            for part in (*root_parts[1:], *expected_key.split("/")[:-1]):
                next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = next_fd
            file_fd = os.open(
                "source.mp4",
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                dir_fd=directory_fd,
            )
            metadata = os.fstat(file_fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != byte_size:
                raise OSError("invalid_original_file")
            file = os.fdopen(file_fd, "rb")
            file_fd = None  # Ownership transfers to the file object.
            return OpenedOriginal(file, metadata)
        except (OSError, ValueError):
            logger.warning("original_read_unavailable")
            raise AppError(
                503, "storage_unavailable", "Vídeo temporariamente indisponível."
            ) from None
        finally:
            if file_fd is not None:
                os.close(file_fd)
            if directory_fd is not None:
                os.close(directory_fd)

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
