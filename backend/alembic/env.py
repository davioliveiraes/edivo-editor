"""Alembic obtains credentials only from typed runtime settings."""

import logging

from pydantic import ValidationError

from alembic import context
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.base import Base
from app.db.session import Database
from app.models.audio_asset import AudioAsset  # noqa: F401
from app.models.edit_spec_revision import EditSpecRevision  # noqa: F401
from app.models.media_asset import MediaAsset  # noqa: F401
from app.models.preview_job import PreviewJob  # noqa: F401
from app.models.project import Project  # noqa: F401
from app.models.render_job import RenderJob  # noqa: F401

logger = logging.getLogger("edivo.migrations")
target_metadata = Base.metadata


def run_migrations() -> None:
    configure_logging("INFO")
    database = None
    try:
        settings = get_settings()
        if context.is_offline_mode():
            context.configure(
                url=settings.database_url.get_secret_value(),
                target_metadata=target_metadata,
                literal_binds=True,
                dialect_opts={"paramstyle": "named"},
            )
            with context.begin_transaction():
                context.run_migrations()
        else:
            database = Database(settings)
            with database.engine.connect() as connection:
                context.configure(connection=connection, target_metadata=target_metadata)
                with context.begin_transaction():
                    context.run_migrations()
    except ValidationError:
        logger.error("migration_configuration_invalid")
        raise SystemExit("Configuração Edivo inválida.") from None
    except Exception:
        logger.error("migration_failed")
        raise SystemExit("Migração indisponível; verifique banco e configuração.") from None
    finally:
        if database is not None:
            database.close()


run_migrations()
