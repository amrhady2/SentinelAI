from __future__ import annotations

from collections.abc import Generator
from sqlmodel import Session, SQLModel, create_engine

from app.config import ensure_sqlite_parent, settings
from app import models  # noqa: F401 - register every table before create_all

ensure_sqlite_parent(settings.database_url)
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
