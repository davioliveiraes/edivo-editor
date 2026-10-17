from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings


class Database:
    def __init__(self, settings: Settings) -> None:
        self.engine: Engine = create_engine(
            settings.database_url.get_secret_value(),
            pool_pre_ping=True,
            pool_size=2,
            max_overflow=2,
            pool_timeout=settings.database_connect_timeout,
            hide_parameters=True,
            connect_args={
                "connect_timeout": settings.database_connect_timeout,
                "options": "-c statement_timeout=3000",
            },
        )
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    def check(self) -> bool:
        try:
            with self.engine.connect() as connection:
                return connection.scalar(text("SELECT 1")) == 1
        except Exception:
            return False

    def close(self) -> None:
        self.engine.dispose()
