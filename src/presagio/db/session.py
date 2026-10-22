"""Database engine, session factory, and a connectivity check."""

import sys
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from presagio.config import get_settings


def make_engine(url: str | None = None, connect_timeout: int | None = None) -> Engine:
    """Build a SQLAlchemy engine; arguments default to the loaded settings."""
    if url is None or connect_timeout is None:
        settings = get_settings()
        url = url if url is not None else settings.database_url
        connect_timeout = (
            connect_timeout if connect_timeout is not None else settings.db_connect_timeout
        )
    return create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": connect_timeout},
    )


@lru_cache
def get_engine() -> Engine:
    """Return the process-wide engine built from settings."""
    return make_engine()


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    """Return the process-wide session factory bound to the default engine."""
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def check_connection(engine: Engine | None = None) -> str:
    """Run SELECT version() and return the server version string; raises on failure."""
    engine = engine if engine is not None else get_engine()
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version()")).scalar_one()
    print(f"OK {engine.url!s}: {version}")
    return version


if __name__ == "__main__":
    try:
        check_connection()
    except Exception as exc:  # report any failure as a non-zero exit for shell use
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
