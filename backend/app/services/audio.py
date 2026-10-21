"""Import MP3 audio with filesystem compensation and a short final DB lock."""

import logging
import unicodedata
from dataclasses import asdict
from pathlib import PurePath
from typing import BinaryIO
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError, database_unavailable
from app.db.session import safe_rollback
from app.integrations.ffmpeg.audio_probe import FFprobeAudioInspector
from app.models.audio_asset import AudioAsset
from app.models.project import Project
from app.repositories.audio import AudioRepository
from app.repositories.projects import ProjectRepository
from app.storage.local import LocalStorage, sanitize_filename

logger = logging.getLogger("edivo.audio")


def require_active_project(session: Session, project_id: UUID, *, for_update: bool = False) -> None:
    if ProjectRepository(session).get_active(project_id, for_update=for_update) is None:
        raise AppError(404, "project_not_found", "Projeto não encontrado.")


def import_audio(
    session: Session,
    settings: Settings,
    project_id: UUID,
    filename: str | None,
    source: BinaryIO,
) -> AudioAsset:
    safe_name = sanitize_filename(filename)
    original_basename = (
        unicodedata.normalize("NFKC", filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    )
    if PurePath(original_basename).suffix.lower() != ".mp3":
        raise AppError(415, "unsupported_audio", "Envie um arquivo com extensão .mp3.")
    storage = LocalStorage(settings)
    staged = None
    storage_key = None
    committed = False
    commit_attempted = False
    preserve_uncertain = False
    try:
        require_active_project(session, project_id)
        # No PostgreSQL transaction is kept open while transferring/probing bytes.
        safe_rollback(session)
        staged = storage.stage_audio(source)
        metadata = FFprobeAudioInspector(settings).inspect(staged.path)
        asset_id = uuid4()
        # Serialize promotion/commit with soft delete. A deleted project cannot receive media.
        require_active_project(session, project_id, for_update=True)
        expected_key = f"originals-audio/{project_id}/{asset_id}/source.mp3"
        asset = AudioAsset(
            id=asset_id,
            project_id=project_id,
            original_filename=safe_name,
            storage_key=expected_key,
            checksum_sha256=staged.checksum_sha256,
            byte_size=staged.byte_size,
            mime_detected="audio/mpeg",
            **asdict(metadata),
        )
        AudioRepository(session).add(asset)
        # Detect constraints/database failures before publication, without a dangling committed row.
        session.flush()
        storage_key = storage.promote_audio(staged, project_id, asset_id)
        commit_attempted = True
        session.commit()
        committed = True
        logger.info("audio_import_completed", extra={"project_id": str(project_id)})
        return asset
    except SQLAlchemyError:
        safe_rollback(session)
        if commit_attempted and storage_key is not None:
            # A lost COMMIT response does not prove the server rolled back. Check using
            # a fresh session; if the DB cannot settle the outcome, preserve the original.
            try:
                with Session(bind=session.get_bind()) as verification:
                    # Wait for the original transaction to settle before observing its result.
                    # This same project row is locked through publication and COMMIT above.
                    verification.scalar(
                        select(Project.id).where(Project.id == project_id).with_for_update()
                    )
                    preserve_uncertain = (
                        verification.scalar(select(AudioAsset.id).where(AudioAsset.id == asset_id))
                        is not None
                    )
            except SQLAlchemyError:
                preserve_uncertain = True
                logger.error("audio_commit_outcome_uncertain")
        logger.warning("audio_import_database_failed")
        raise database_unavailable() from None
    except OSError:
        safe_rollback(session)
        logger.warning("audio_import_storage_failed")
        raise AppError(
            503, "storage_unavailable", "Armazenamento temporariamente indisponível."
        ) from None
    except BaseException:
        safe_rollback(session)
        logger.warning("audio_import_rejected")
        raise
    finally:
        if staged is not None:
            storage.discard_staging(staged.path)
        if storage_key is not None and not committed and not preserve_uncertain:
            storage.discard_original(storage.root / storage_key)


def list_audio(session: Session, project_id: UUID, limit: int, offset: int):
    require_active_project(session, project_id)
    return AudioRepository(session).list_for_project(project_id, limit, offset)


def get_audio(session: Session, project_id: UUID, audio_id: UUID) -> AudioAsset:
    require_active_project(session, project_id)
    asset = AudioRepository(session).get_for_project(project_id, audio_id)
    if asset is None:
        raise AppError(404, "audio_not_found", "Áudio não encontrado neste projeto.")
    return asset
