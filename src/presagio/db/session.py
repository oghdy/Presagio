"""Database engine, session factory, and a connectivity check."""

import sys
from functools import lru_cache

from sqlalchemy import URL, Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from presagio.config import DEFAULT_CONNECT_TIMEOUT, get_settings


def make_engine(url: str | URL | None = None, connect_timeout: int | None = None) -> Engine:
    """Build an engine; without a url, url and timeout come from settings (.env)."""
    if url is None:
        settings = get_settings()
        url = settings.database_url
        if connect_timeout is None:
            connect_timeout = settings.db_connect_timeout
    elif connect_timeout is None:
        # An explicit url must not require .env (e.g. Alembic or tests passing their own url).
        connect_timeout = DEFAULT_CONNECT_TIMEOUT
    return create_engine(
        url,
        pool_pre_ping=True,
        # Session time zone UTC (schema v1 rule 1-2.6): timestamptz values print as UTC.
        connect_args={"connect_timeout": connect_timeout, "options": "-c timezone=UTC"},
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
