"""Tests for settings loading and the database connection helpers."""

import time

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from presagio.config import Settings
from presagio.db.session import check_connection, make_engine


def test_settings_read_database_url_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """DATABASE_URL and DB_CONNECT_TIMEOUT are read from the environment."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db.example:6543/d")
    monkeypatch.setenv("DB_CONNECT_TIMEOUT", "7")
    settings = Settings(_env_file=None)
    assert settings.database_url == "postgresql+psycopg://u:p@db.example:6543/d"
    assert settings.db_connect_timeout == 7


def test_settings_require_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without DATABASE_URL, settings fail loudly instead of guessing a default."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_check_connection_fails_fast_when_unreachable() -> None:
    """A closed local port raises OperationalError well within the timeout."""
    engine = make_engine("postgresql+psycopg://presagio:presagio@127.0.0.1:1/presagio", 1)
    start = time.monotonic()
    with pytest.raises(OperationalError):
        check_connection(engine)
    assert time.monotonic() - start < 5
    engine.dispose()


def test_check_connection_returns_server_version(db_engine: Engine) -> None:
    """check_connection reaches the configured PostgreSQL and reports its version."""
    version = check_connection(db_engine)
    assert version.startswith("PostgreSQL")


def test_session_runs_query(db_engine: Engine) -> None:
    """A Session bound to the engine can run a trivial query."""
    with Session(db_engine) as session:
        assert session.execute(text("SELECT 1")).scalar_one() == 1
