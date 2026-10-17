"""Request-scoped synchronous sessions; services explicitly own their commit."""

import logging
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import database_unavailable
from app.db.session import safe_rollback

logger = logging.getLogger("edivo.database")


def get_session(request: Request) -> Iterator[Session]:
    session = None
    try:
        session = request.app.state.database.sessions()
        yield session
    except SQLAlchemyError:
        safe_rollback(session)
        logger.warning("database_operation_failed")
        raise database_unavailable() from None
    except Exception:
        safe_rollback(session)
        raise
    finally:
        if session is not None:
            try:
                session.close()
            except SQLAlchemyError:
                logger.warning("database_session_close_failed")


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


SessionDependency = Annotated[Session, Depends(get_session)]
