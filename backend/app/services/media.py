"""Import source media with filesystem compensation and a short final DB lock."""

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
from app.integrations.ffmpeg.probe import FFprobeInspector
from app.models.media_asset import MediaAsset
from app.models.project import Project
from app.repositories.media import MediaRepository
from app.repositories.projects import ProjectRepository
from app.storage.local import LocalStorage, sanitize_filename

logger = logging.getLogger("edivo.media")


def require_active_project(session: Session, project_id: UUID, *, for_update: bool = False) -> None:
    if ProjectRepository(session).get_active(project_id, for_update=for_update) is None:
        raise AppError(404, "project_not_found", "Projeto não encontrado.")


def import_media(
    session: Session,
    settings: Settings,
    project_id: UUID,
    filename: str | None,
    source: BinaryIO,
) -> MediaAsset:
    safe_name = sanitize_filename(filename)
    original_basename = (
        unicodedata.normalize("NFKC", filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    )
    if PurePath(original_basename).suffix.lower() != ".mp4":
        raise AppError(415, "unsupported_media", "Envie um arquivo com extensão .mp4.")
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
        staged = storage.stage(source)
        metadata = FFprobeInspector(settings).inspect(staged.path)
        asset_id = uuid4()
        # Serialize promotion/commit with soft delete. A deleted project cannot receive media.
        require_active_project(session, project_id, for_update=True)
        expected_key = f"originals/{project_id}/{asset_id}/source.mp4"
        asset = MediaAsset(
            id=asset_id,
            project_id=project_id,
            kind="source_video",
            original_filename=safe_name,
            storage_key=expected_key,
            checksum_sha256=staged.checksum_sha256,
            byte_size=staged.byte_size,
            mime_detected="video/mp4",
            **asdict(metadata),
        )
        MediaRepository(session).add(asset)
        # Detect constraints/database failures before publication, without a dangling committed row.
        session.flush()
        storage_key = storage.promote(staged, project_id, asset_id)
        commit_attempted = True
        session.commit()
        committed = True
        logger.info("media_import_completed", extra={"project_id": str(project_id)})
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
                        verification.scalar(select(MediaAsset.id).where(MediaAsset.id == asset_id))
                        is not None
                    )
            except SQLAlchemyError:
                preserve_uncertain = True
                logger.error("media_commit_outcome_uncertain")
        logger.warning("media_import_database_failed")
        raise database_unavailable() from None
    except OSError:
        safe_rollback(session)
        logger.warning("media_import_storage_failed")
        raise AppError(
            503, "storage_unavailable", "Armazenamento temporariamente indisponível."
        ) from None
    except BaseException:
        safe_rollback(session)
        logger.warning("media_import_rejected")
        raise
    finally:
        if staged is not None:
            storage.discard_staging(staged.path)
        if storage_key is not None and not committed and not preserve_uncertain:
            storage.discard_original(storage.root / storage_key)


def list_media(session: Session, project_id: UUID, limit: int, offset: int):
    require_active_project(session, project_id)
    return MediaRepository(session).list_for_project(project_id, limit, offset)


def get_media(session: Session, project_id: UUID, media_id: UUID) -> MediaAsset:
    require_active_project(session, project_id)
    asset = MediaRepository(session).get_for_project(project_id, media_id)
    if asset is None:
        raise AppError(404, "media_not_found", "Mídia não encontrada neste projeto.")
    return asset
