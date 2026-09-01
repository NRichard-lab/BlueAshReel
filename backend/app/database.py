from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import Connection, Engine, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_config


class Base(DeclarativeBase):
    pass


def _sqlite_connect_args(url: str) -> dict[str, object]:
    return {"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {}


def create_database_engine(url: str | None = None) -> Engine:
    database_url = url or get_config().database_url
    if database_url.startswith("sqlite:///") and ":memory:" not in database_url:
        raw_path = database_url.removeprefix("sqlite:///")
        if raw_path:
            Path(raw_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    result = create_engine(
        database_url,
        connect_args=_sqlite_connect_args(database_url),
        pool_pre_ping=True,
        future=True,
    )
    if database_url.startswith("sqlite"):
        event.listen(result, "connect", _configure_sqlite)
    return result


def _configure_sqlite(dbapi_connection: object, _connection_record: object) -> None:
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


engine = create_database_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def database_is_ready(db_engine: Engine | Connection = engine) -> bool:
    try:
        if isinstance(db_engine, Connection):
            db_engine.execute(text("SELECT 1"))
            db_engine.execute(text("SELECT 1 FROM application_settings LIMIT 1"))
        else:
            with db_engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                connection.execute(text("SELECT 1 FROM application_settings LIMIT 1"))
        return True
    except Exception:
        return False
