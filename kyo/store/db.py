"""Database connection and sessions."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_URL = "sqlite:///kyo_shop.sqlite3"


def now() -> datetime:
    """The current time in UTC, without a time zone, as the database stores it."""
    return datetime.now(UTC).replace(tzinfo=None)


def database_url() -> str:
    return os.environ.get("KYO_DATABASE_URL", DEFAULT_URL)


def make_engine(url: str | None = None) -> Engine:
    url = url or database_url()
    is_sqlite = url.startswith("sqlite")
    engine = create_engine(
        url, connect_args={"check_same_thread": False, "timeout": 30} if is_sqlite else {}
    )
    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _configure(connection, record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine


def make_sessions(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


@contextmanager
def transaction(sessions: sessionmaker[Session]) -> Iterator[Session]:
    """A session that commits on success and rolls back on any error."""
    session = sessions()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
