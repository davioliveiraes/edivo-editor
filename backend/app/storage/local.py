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
        # Path.mkdir(parents=True) applies mode only to the final component.
        # Create each missing ancestor privately, without changing existing modes.
        for part in (*reversed(directory.parents), directory):
            part.mkdir(mode=0o700, exist_ok=True)
            if part.is_symlink() or not part.is_dir():
                raise OSError("unsafe_storage_directory")
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
        return self._stage(source, self.settings.upload_max_bytes, ".mp4")

    def stage_audio(self, source: BinaryIO) -> StagedFile:
        return self._stage(source, self.settings.audio_upload_max_bytes, ".mp3")

    def _stage(self, source: BinaryIO, max_bytes: int, suffix: str) -> StagedFile:
        path: Path | None = None
        try:
            directory = self.prepare_temp()
            fd, name = tempfile.mkstemp(prefix="upload-", suffix=suffix, dir=directory)
            path = Path(name)
            byte_size = 0
            checksum = hashlib.sha256()
            with os.fdopen(fd, "wb") as target:
                while chunk := source.read(CHUNK_BYTES):
                    byte_size += len(chunk)
                    if byte_size > max_bytes:
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
        return self._promote(staged, project_id, asset_id, "originals", "source.mp4")

    def promote_audio(self, staged: StagedFile, project_id: UUID, asset_id: UUID) -> str:
        return self._promote(staged, project_id, asset_id, "originals-audio", "source.mp3")

    def _promote(
        self,
        staged: StagedFile,
        project_id: UUID,
        asset_id: UUID,
        namespace: str,
        source_name: str,
    ) -> str:
        destination: Path | None = None
        created_directory = False
        try:
            parent = self._directory(self.root / namespace / str(project_id))
            destination = parent / str(asset_id)
            # Atomic exclusive mkdir prevents accidentally adopting another asset directory.
            destination.mkdir(mode=0o700, exist_ok=False)
            created_directory = True
            target = destination / source_name
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
                self.discard_original(destination / source_name)
            raise AppError(
                503, "storage_unavailable", "Não foi possível preservar o arquivo original."
            ) from None

    def open_original(
        self, storage_key: str, project_id: UUID, asset_id: UUID, byte_size: int
    ) -> OpenedOriginal:
        expected_key = f"originals/{project_id}/{asset_id}/source.mp4"
        return self._open_managed(storage_key, expected_key, byte_size)

    def open_audio(
        self, storage_key: str, project_id: UUID, asset_id: UUID, byte_size: int
    ) -> OpenedOriginal:
        expected_key = f"originals-audio/{project_id}/{asset_id}/source.mp3"
        return self._open_managed(storage_key, expected_key, byte_size)

    def open_preview(
        self, storage_key: str, project_id: UUID, job_id: UUID, byte_size: int
    ) -> OpenedOriginal:
        return self._open_managed(
            storage_key, f"previews/{project_id}/{job_id}/preview.mp4", byte_size
        )

    def open_render(
        self, storage_key: str, project_id: UUID, job_id: UUID, byte_size: int
    ) -> OpenedOriginal:
        return self._open_managed(
            storage_key, f"renders/{project_id}/{job_id}/final.mp4", byte_size
        )

    def promote_preview(self, staged: StagedFile, project_id: UUID, job_id: UUID) -> str:
        return self._promote_artifact(
            staged, project_id, job_id, "previews", "preview.mp4", "preview"
        )

    def promote_render(self, staged: StagedFile, project_id: UUID, job_id: UUID) -> str:
        return self._promote_artifact(staged, project_id, job_id, "renders", "final.mp4", "render")

    def _promote_artifact(
        self,
        staged: StagedFile,
        project_id: UUID,
        job_id: UUID,
        namespace: str,
        filename: str,
        error_prefix: str,
    ) -> str:
        """Publish exclusively; an ambiguous publication is preserved for reconciliation.

        The worker holds the project/job locks and checks its lease before and
        after this operation. Unlike upload compensation, no failure here may
        remove a canonical artifact that another transaction could have committed.
        """
        message = (
            "Não foi possível publicar a prévia."
            if error_prefix == "preview"
            else "Não foi possível publicar o vídeo."
        )
        try:
            if not self.root.is_dir():
                raise OSError("storage_missing")
            self.ensure_space(staged.byte_size)
            parent = self._directory(self.root / namespace / str(project_id))
            destination = parent / str(job_id)
            try:
                destination.mkdir(mode=0o700, exist_ok=False)
            except FileExistsError:
                raise AppError(
                    503,
                    f"{error_prefix}_publication_interrupted",
                    message,
                ) from None
            target = destination / filename
            source_fd = os.open(
                staged.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
            )
            with os.fdopen(source_fd, "rb") as source:
                metadata = os.fstat(source.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != staged.byte_size:
                    raise OSError("invalid_preview_staging")
                os.fchmod(source.fileno(), stat.S_IRUSR)
                os.fsync(source.fileno())
                os.link(staged.path, target, follow_symlinks=False)
                published = target.stat(follow_symlinks=False)
                if (published.st_dev, published.st_ino) != (metadata.st_dev, metadata.st_ino):
                    raise OSError("preview_staging_changed")
            for directory in (destination, parent):
                directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            self.discard_staging(staged.path)
            return target.relative_to(self.root).as_posix()
        except OSError:
            raise AppError(503, "storage_unavailable", message) from None

    def _open_managed(self, storage_key: str, expected_key: str, byte_size: int) -> OpenedOriginal:
        """Open only the authorized original, without following any path symlink.

        Directory-relative opens pin every parent inode, including root ancestors.
        Subsequent reads use the returned descriptor, never a second pathname open.
        O_NONBLOCK also prevents an unexpected FIFO from blocking before fstat.
        """
        directory_fd = None
        file_fd = None
        try:
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
                expected_key.rsplit("/", 1)[-1],
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
                503, "storage_unavailable", "Arquivo temporariamente indisponível."
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
