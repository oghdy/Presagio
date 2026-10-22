"""Shared pytest fixtures for the Presagio test suite."""

import os
import socket
from collections.abc import Callable, Iterator

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError

from presagio.config import Settings
from presagio.db.session import make_engine

# Short on purpose: an unreachable database must not slow the test run down.
TEST_DB_CONNECT_TIMEOUT = 2


def _skip_or_fail(reason: str) -> None:
    """Skip the test, or fail it when PRESAGIO_REQUIRE_DB=1 demands a real database."""
    if os.environ.get("PRESAGIO_REQUIRE_DB") == "1":
        pytest.fail(f"PRESAGIO_REQUIRE_DB=1 but {reason}")
    pytest.skip(reason)


def _connect_test_engine(url: str) -> Engine:
    """Return a connected engine; skip if no server listens, fail if the server rejects us."""
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
def connect_test_engine() -> Callable[[str], Engine]:
    """Expose the skip/fail connection logic so tests can exercise it directly."""
    return _connect_test_engine


@pytest.fixture
def db_engine() -> Iterator[Engine]:
    """Engine for the configured database; skips if it is not configured or not listening."""
    try:
        settings = Settings()
    except ValidationError:
        _skip_or_fail("DATABASE_URL is not set (no env var and no .env)")
    engine = _connect_test_engine(settings.database_url)
    yield engine
    engine.dispose()
