"""Shared pytest fixtures for the Presagio test suite."""

import os
import socket
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import URL, Connection, Engine, make_url, text
from sqlalchemy.exc import OperationalError

from presagio.config import Settings
from presagio.db.session import make_engine

# Short on purpose: an unreachable database must not slow the test run down.
TEST_DB_CONNECT_TIMEOUT = 2

REPO_ROOT = Path(__file__).resolve().parents[1]


def _skip_or_fail(reason: str) -> None:
    """Skip the test, or fail it when PRESAGIO_REQUIRE_DB=1 demands a real database."""
    if os.environ.get("PRESAGIO_REQUIRE_DB") == "1":
        pytest.fail(f"PRESAGIO_REQUIRE_DB=1 but {reason}")
    pytest.skip(reason)


def _connect_test_engine(url: URL) -> Engine:
    """Return a connected engine; skip if no server listens, fail if the server rejects us."""
    # Takes a URL object (its repr masks the password) and hides this frame from pytest
    # tracebacks, so a failing run never prints database credentials.
    __tracebackhide__ = True
    engine = make_engine(url, connect_timeout=TEST_DB_CONNECT_TIMEOUT)
    host, port = engine.url.host or "localhost", engine.url.port or 5432
    # Probe TCP first: "nothing listening" means no database here (skip), while any error
    # after the port answered means the settings are wrong (wrong password, missing db).
    try:
        socket.create_connection((host, port), timeout=TEST_DB_CONNECT_TIMEOUT).close()
    except OSError as exc:
        engine.dispose()
        _skip_or_fail(f"no database listening at {host}:{port}: {exc}")
    try:
        with engine.connect():
            pass
    except OperationalError as exc:
        engine.dispose()
        rejected = str(exc.orig)
    else:
        return engine
    # Fail outside the except block so the report shows the cause, not a chained traceback.
    pytest.fail(f"database at {host}:{port} is listening but rejected the connection:\n{rejected}")


@pytest.fixture
def connect_test_engine() -> Callable[[URL], Engine]:
    """Expose the skip/fail connection logic so tests can exercise it directly."""
    return _connect_test_engine


@pytest.fixture
def db_engine() -> Iterator[Engine]:
    """Engine for the configured database; skips if it is not configured or not listening."""
    __tracebackhide__ = True  # keep the settings (with the password) out of tracebacks
    try:
        url = make_url(Settings().database_url)
    except ValidationError:
        _skip_or_fail("DATABASE_URL is not set (no env var and no .env)")
    engine = _connect_test_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def make_test_database() -> Iterator[Callable[[], URL]]:
    """Factory for fresh, empty databases next to the configured one, dropped after the run."""
    # Migration tests drop every table (downgrade base), so they never touch the configured
    # database itself: each call creates <db>_test_<random> on the same server instead.
    __tracebackhide__ = True
    try:
        base_url = make_url(Settings().database_url)
    except ValidationError:
        _skip_or_fail("DATABASE_URL is not set (no env var and no .env)")
    admin = _connect_test_engine(base_url).execution_options(isolation_level="AUTOCOMMIT")
    created: list[str] = []

    def _create() -> URL:
        """Create one empty database and return its URL."""
        name = f"{base_url.database}_test_{uuid.uuid4().hex[:8]}"
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        created.append(name)
        return base_url.set(database=name)

    yield _create
    with admin.connect() as conn:
        for name in created:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def _alembic_config(connection: Connection) -> Config:
    """Alembic config for the repo's alembic.ini that migrates the given open connection."""
    config = Config(str(REPO_ROOT / "alembic.ini"))
    # alembic/env.py uses this connection instead of DATABASE_URL and keeps pytest's logging.
    config.attributes["connection"] = connection
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture(scope="session")
def alembic_config() -> Callable[[Connection], Config]:
    """Expose the Alembic config builder so tests can drive migrations programmatically."""
    return _alembic_config
