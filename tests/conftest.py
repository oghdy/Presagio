"""Shared pytest fixtures for the Presagio test suite."""

import os
from collections.abc import Iterator

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


@pytest.fixture
def db_engine() -> Iterator[Engine]:
    """Engine for the configured database; skips if it is not configured or not reachable."""
    try:
        settings = Settings()
    except ValidationError:
        _skip_or_fail("DATABASE_URL is not set (no env var and no .env)")
    engine = make_engine(settings.database_url, connect_timeout=TEST_DB_CONNECT_TIMEOUT)
    try:
        with engine.connect():
            pass
    except OperationalError as exc:
        engine.dispose()
        first_line = str(exc.orig).splitlines()[0]
        _skip_or_fail(f"database not reachable at {engine.url!s}: {first_line}")
    yield engine
    engine.dispose()
