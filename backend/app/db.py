"""Database engine, session factory, and declarative base."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

_connect_args = {}
if settings.database_url.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(retries: int = 30, delay: float = 2.0) -> None:
    """Create tables and seed defaults. Safe to call repeatedly.

    Retries the initial connection so it tolerates Docker Compose start
    ordering (the database may not accept connections immediately).
    """
    import time

    from sqlalchemy.exc import OperationalError

    # Import models so they register on the metadata before create_all.
    from app import models  # noqa: F401
    from app.services.settings_store import seed_defaults

    for attempt in range(1, retries + 1):
        try:
            Base.metadata.create_all(bind=engine)
            break
        except OperationalError:
            if attempt == retries:
                raise
            time.sleep(delay)
    with SessionLocal() as db:
        seed_defaults(db)
        db.commit()
